#!/usr/bin/python3

import argparse
import curses
import datetime
import logging
import math
import os
import pickle
import re
import sys
import json
import time
import fcntl
import select
import functools
import threading
import subprocess
from copy import deepcopy
from curses import wrapper
from os.path import isfile, join
from tabulate import tabulate
from platform import node

import anomaly_graph as ml

EQUAL_ROWS_THRESHOLD = 0.1
ANOMALY_MAX_THRESHOLD = 0.5
ANOMALY_DEVIATION_THRESHOLD = 0.3

learning = True

DISPLAY_REFRESH_FREQUENCY = 5

gauges = ['_buffered', '_active', 'uptime', 'concurrency', '_allocated', '_size', '.live', '.state', '_connections',
					'version', '_expiring', '_epoch', '_clusters', '_clusters', '_healthy','_degraded', '_total',	'_weight',
					'.healthy', '_open', '_cx', '_pending', '_rq', '_retries', 	'.size', '_per_host', 'gradient', '_limit', 
					'_size', '_msecs', '_faults', '_warming', '_draining', '_started', '_keys', '_layers', '.active', '_requests']

exclude_keys = ['version', 'istio', 'prometheus', 'grafana', 'nginx', 'kube', 'jaeger', 'BlackHole', 'grpc', 'zipkin', 'mixer', 'rq_timeout', 'external', 'internal']
include_keys = ['rq_time']

# Next structures are to be entered by user later, now hardcoded for bookinfo app
sibling_prefix = 'reviews-'
siblings = ['reviews-v1', 'reviews-v2', 'reviews-v3']

class StderrWriter(object):
	def __init__(self, logger_):
		self.m_logger = logger_

	def write(self, message_):
		for line in message_.rstrip().splitlines():
			 self.m_logger.error(line.rstrip())

class GeneralLogLevelFilter(object):
	def filter(self, record_):
		if "tensorflow" == record_.name:
			return False

		return record_.levelno < logging.ERROR

class TensorflowFilter(object):
	def filter(self, record_):
		return "tensorflow" == record_.name

general_logger = logging.getLogger()
general_formater = logging.Formatter('%(asctime)s.%(msecs)03d %(levelname)s %(thread)d: %(message)s', datefmt='%m-%d %H:%M:%S')
error_handler = logging.FileHandler('monitor_envoy_error.log')
error_handler.setLevel(logging.ERROR)
error_handler.setFormatter(general_formater)
general_handler = logging.FileHandler('monitor_envoy.log')
general_handler.setLevel(logging.NOTSET)
general_handler.addFilter(GeneralLogLevelFilter())
general_handler.setFormatter(general_formater)
tensorflow_handler = logging.FileHandler('tensorflow.log')
tensorflow_handler.setLevel(logging.NOTSET)
tensorflow_handler.addFilter(TensorflowFilter())
tensorflow_handler.setFormatter(general_formater)
general_logger.setLevel(logging.NOTSET)
general_logger.handlers.clear()
general_logger.addHandler(error_handler)
general_logger.addHandler(general_handler)
general_logger.addHandler(tensorflow_handler)

os.environ['TF_CPP_MIN_LOG_LEVEL'] = '3'  # FATAL

sys.stderr = StderrWriter(general_logger)
monitor = None

def exclude_row(key, value):
	for exclude in exclude_keys:
		if exclude in key:
			return True
	if not '9080' in key:
		return True
	for include in include_keys:
		if include in key:
			return False
	return True

def process_ml(filter=''):
	global monitor
	general_logger.info("Starting ML processing")
	while True:
		try:
			monitor.ml_anomalies = ml.process_anomalies(general_logger, filter)
		except Exception as e:
			general_logger.error("ERROR in ML thread: " + str(e))
		time.sleep(1)

def confirm_anomalies():
	global monitor
	general_logger.info("Starting ML anomalies confirming")
	while True:
		try:
			if len(ml.column_filter):
				general_logger.info("ML running confirming for %s", "".join(ml.column_filter))
				monitor.ml_anomalies = ml.process_anomalies(general_logger, ml.column_filter)
			else:
				ml.anomalies_found = {}
				ml_normals_found = {}
				monitor.ml_anomalies = []
		except Exception as e:
			logging.error("ERROR in ML thread")
			logging.error(e, exc_info=True)
		time.sleep(1)

class Results:
	cols = ['name', 'kind', 'eq', 'anom', 'min', 'avg', 'max', 'dev', 'navg', 'ndev', 'val', 'nval']
	cols_props = {'name': 'name', 'kind': 'kind',
				'eq': 'equals_count', 'min': 'min', 'avg': 'avg', 'max': 'max', 'dev': 'dev',
				'navg': 'norm_avg', 'ndev': 'norm_dev', 'val': 'last_value', 'nval': 'norm_last_value',
				'd_eq': 'diff_equals_count', 'd_max': 'diff_max', 'd_dev': 'diff_dev', 'd_ndev': 'diff_norm_dev',
				'anom': 'anomalies'}
	# function tabulate_values holds the code collecting the values for the above columns
	
	def __init__(self, name, kind):
		# Name: name of the metric
		self.name = name
		# kind: 'histo', 'counter', 'gauge' - counters are growing
		self.kind = kind
		# Start: first number in sequence for counters
		self.start = ''
		# Counter: last increment of the counter
		self.counter = None
		# Filtered out: the metric should be skipped everywhere
		self.filtered_out = False
		# Primary equal: for those metrics equaled out by some other metric
		self.primary_equal = None
		# Equaled out: is a part of some other equaled group
		self.equaled_out = False
		# Equals: a group of equal metrics names contained in primary equal
		self.equals = set()
		# Equals count: a number of equals in the group
		self.equals_count = 0
		# Stats: ResultStats statistics object
		self.stats = None
		# Norm stats: normalized ResultStats statistics object
		self.norm_stats = None
		# Empty: sign that this metric never had any value
		self.empty = True
		# Last value: last read value
		self.last_value = None
		# Norm last value: normalized last read value
		self.norm_last_value = None
		# Count: number of values for this metric encountered so far
		self.count = 0

		# Stats: various math stats
		# Min: minimum
		self.min = float('inf')
		# Avg: average
		self.avg = 0.0
		# Max: maximum
		self.max = 0.0
		# Var: variance
		self.var = 0.0
		# Dev: deviation
		self.dev = 0.0
		
		# Norm stats: normalized stats - min is always 0 and max is always 1
		# Avg: normalized average
		self.norm_avg = 0.0
		# Dev: normalized deviation
		self.norm_dev = 0.0
		
		# Reference stats: stats frozen after learning stage and diffs with current stats for anomalies:
		# Ref count: point of freeze
		self.ref_count = 0
		# Ref equals count: reference number of equals
		self.ref_equals_count = 0
		# Ref max: reference maximum
		self.ref_max = 0.0
		# Ref deviation: reference deviation
		self.ref_dev = 0.0
		# Ref normalized deviation: reference normalize deviation
		self.ref_norm_dev = 0.0
		# Equals count diff: difference between current and reference equals count
		self.diff_equals_count = 0
		# Max diff: difference between current and reference max values
		self.diff_max = 0.0
		# Deviation diff: difference between current and reference deviations
		self.diff_dev = 0.0
		# Normalized deviation diff: difference between current and reference normalized deviations
		self.diff_norm_dev = 0.0
		
		# Anomalies: count of anomalies of metrics
		self.anomalies = 0
		self.anomaly_unequal = 0
		self.anomaly_maxed = 0
		self.anomaly_deviated = 0
		self.anomaly_ml = 0


	def get(self, prop):
		return getattr(self, prop)
	
	def discard(self):
		return self.empty or self.filtered_out or self.equaled_out or self.zeroed_out()
	
	def zeroed_out(self):
		return self.min == float('inf') or self.max == 0

	def tabulate_values(self):
		return [self.name, self.kind, self.equals_count, self.anomalies,
				self.min, self.avg, self.max, self.dev,
				self.norm_avg, self.norm_dev, self.last_value, self.norm_last_value]
				#self.diff_equals_count, self.diff_max, self.diff_dev, self.diff_norm_dev]
	
	def is_gauge(key):
		for item in gauges:
			if key.endswith(item):
				return True
		return False
	
	def is_equal(self, result):
		return (abs(self.norm_last_value - result.norm_last_value) <= EQUAL_ROWS_THRESHOLD and
				abs(self.norm_avg - result.norm_avg) <= EQUAL_ROWS_THRESHOLD)

	def normalize(self, value):
		return value / self.max
	
	def return_to_normal(self):
		
		self.equals_count = self.ref_equals_count
		self.max = self.ref_max
		self.dev = self.ref_dev
		self.norm_dev = self.ref_norm_dev
		self.anomalies = 0
		self.anomaly_unequal = 0
		self.anomaly_maxed = 0
		self.anomaly_deviated = 0
		self.anomaly_ml = 0

	def set_reference(self):
		self.ref_count = self.count
		self.ref_equals_count = self.equals_count
		self.ref_max = self.max
		self.ref_dev = self.dev
		self.ref_norm_dev = self.norm_dev
		self.diff_equals_count = 0
		self.diff_max = 0.0
		self.diff_dev = 0.0
		self.diff_norm_dev = 0.0
		self.anomalies = 0
		self.anomaly_unequal = 0
		self.anomaly_maxed = 0
		self.anomaly_deviated = 0
		self.anomaly_ml = 0

	# Verifies if result is still equaled out by its primary equal and removes equality if not
	def verify_equaled_out(self):
		if (not (self.filtered_out or self.zeroed_out() or self.empty) and
				self.equaled_out and not self.primary_equal.empty and not self.is_equal(self.primary_equal)):
			self.equaled_out = False
			self.primary_equal.equals.remove(self.name)
			if learning == False:
				self.anomaly_unequal = abs(self.norm_last_value - self.primary_equal.norm_last_value)
				self.anomalies += 1
			
	# Verify if previously non-equal results are equal and set the grouping if they are (expects non-discard() self and result)
	def verify_is_equal(self, result):
		if self.is_equal(result) and self.primary_equal == result.primary_equal:
			self.equaled_out = True
			result.equals.add(self.name)
			self.primary_equal = result
			return True
		else:
			return False

	def process_stat(self, value):
		if value < self.min:
			self.min = value
		delta = value - self.avg
		self.avg = self.avg + delta  / self.count
		if value > self.max:
			self.max = value
		self.var = (self.var * (self.count - 1) + delta * (value - self.avg)) / self.count
		self.dev = math.sqrt(self.var)

	def process_value(self, value):
		if value == '':
			self.last_value = None
			self.norm_last_value = None
			self.empty = True
			return
		
		value = float(value)
		
		# Normalize counters
		if self.kind == 'C':
			old_value = self.counter
			if old_value:
				self.counter = value
				value = value - old_value
			else:
				self.counter = value											
				self.start = value
				value = 0.0

		# Calculate stats
		self.empty = False
		self.last_value = value
		self.count += 1
		self.process_stat(value)
		if self.max != 0:
			norm_value = self.normalize(value)
			self.norm_avg = self.normalize(self.avg)
			self.norm_dev = self.normalize(self.dev)
		else:
			norm_value = 0.0
		self.norm_last_value = norm_value
		if learning:
			self.diff_equals_count = 0
			self.diff_max = 0.0
			self.diff_dev = 0.0
			self.diff_norm_dev = 0.0
		else:
			# We'll be looking for metrics with less equals than in reference, which means less uniformity
			self.diff_equals_count = self.ref_equals_count - self.equals_count
			# We'll be looking for metrics with increased values
			self.diff_max = self.max - self.ref_max
			self.diff_dev = self.dev - self.ref_dev
			self.diff_norm_dev = self.norm_dev - self.ref_norm_dev
			self.anomaly_maxed = 0
			self.anomaly_deviated = 0
			if self.diff_max > self.ref_max * (1 + ANOMALY_MAX_THRESHOLD):
				general_logger.info("MAXED value in %s - %s %s %s", self.name, str(value), str(self.diff_max), str(self.ref_max))
				self.anomaly_maxed = self.diff_max
				self.anomalies += 1
			if self.diff_norm_dev > ANOMALY_DEVIATION_THRESHOLD:
				self.anomaly_deviated = self.diff_norm_dev
				self.anomalies += 1

			if self.name in ml.anomalies_found:
				self.anomaly_ml = 1
				self.anomalies += 1
			elif self.anomaly_ml == 1:
				self.anomaly_ml = 0
				self.anomalies -= 1


class Pod:
	path = ''
	pods_info = {}

	def __init__(self, name, path):
		Pod.path = path
		self.name = name
		self.full_name = ''
		self.node = ''
		self.processed_files = 0
		self.stats = {}
		self.results = {}
		self.matrix = {}
		self.files = set()
		self.series_count = 0
		self.metrics_count = 0
		self.top = []
		self.unique = 0
		self.empty = 0
		self.filtered_out_keys = set()
		self.filtered_out = 0
		self.equaled_out = 0
		self.zeroed_out = 0
		self.anomalies = 0
		self.anomaly_unequal = 0
		self.anomaly_maxed = 0
		self.anomaly_deviated = 0
		self.anomaly_ml = 0
		self.suspected_anomalies = []

	def return_to_normal(self):
		general_logger.info("Returning to normal pod %s", self.full_name)
		for result in self.results.values():
			result.return_to_normal()
		self.anomaly_unequal = 0
		self.anomaly_maxed = 0
		self.anomaly_deviated = 0
		self.anomaly_ml = 0
		self.suspected_anomalies = []

	def set_reference(self):
		for result in self.results.values():
			result.set_reference()

	def add_value(self, key, value, empty, kind):
		global monitor
		if not key in self.results:
			result = Results(key, kind) 
			self.results[key] = result
		else:
			result = self.results[key]
		if not key in self.matrix:
			self.matrix[key] = []
		if not key in monitor.global_matrix:
			monitor.global_matrix[key] = []
		if value == empty:
			value = ''
			mvalue = 0
		else:
			mvalue = float(value)
		self.matrix[key].append(mvalue)
		monitor.global_matrix[key].append(mvalue)
		result.process_value(value)

	def shorten(self, key):
		key = key.replace('cluster', 'c')
		key = key.replace('listener', 'lsr')
		key = key.replace('inbound', 'in')
		key = key.replace('outbound', 'out')
		key = key.replace('default', 'def')
		key = key.replace('manager', 'm')
		key = key.replace('server', 'srvr')
		key = key.replace('socket', 'sckt')
		key = key.replace('context', 'ctxt')
		key = key.replace('factory', 'fctry')
		key = key.replace('update', 'upd')
		return key

	def generate_key(self, key):
		return self.name + '|' + key #self.shorten(key)

	@classmethod
	def get_node(cls, timestamp, pod_name):
		if not Pod.pods_info.get(timestamp):
			Pod.pods_info[timestamp] = {}
			with open(join(Pod.path, 'pods.' + timestamp)) as f:
				fcontents = f.read()
				contents = fcontents.splitlines()
				it = iter(contents)
				for row in it:
					pod = row.split(':')[1].lstrip()
					node = next(it).split(':')[1].lstrip()
					Pod.pods_info[timestamp][pod] = node
		return Pod.pods_info[timestamp][pod_name]
		
	def read_envoy_data(self, fname, file_series, pods_count):
		with open(join(self.path, fname), 'r') as f:
			fcontents = f.read()
			contents = fcontents.splitlines()
			pod_name, timestamp = fname.split('.')
			# Check that time series is complete in regard to all pods
			fs_timestamp = file_series.get(timestamp)
			if not fs_timestamp or len(fs_timestamp) < pods_count:
				return False

			if self.full_name != pod_name:
				general_logger.info("Returning to normal for changed pod old %s new %s", self.full_name, pod_name)
				self.full_name = pod_name
				self.return_to_normal()

			if not timestamp in self.stats:
				self.stats[timestamp] = {}
				self.node = Pod.get_node(timestamp, self.full_name)
			for row in contents:
				row_split = row.split(':')
				try:
					key = self.generate_key(row_split[0])
					value = row_split[1]
				except Exception as e:
					logging.error("ERROR parsing value: %s %s", fname, row)
					logging.error(e, exc_info=True)
					continue
				if exclude_row(key, value):
					self.filtered_out_keys.add(key)
					continue
				if 'P0(' in value:
					histogram = value.split()
					for hval in histogram:
						hval_split = re.split('[(,)]', hval)
						if hval_split[0] in ['P75']:
							# Not adding the postfix here because it's a single percentile we take now and also
							# When there is "No recorded value" it's being added as a non-histo metric without "|P95" postfix
							# and ruins evenness of the global matrix
							hkey = key # + '|' + hval_split[0]
							self.stats[timestamp][key] = hval_split[1]
							self.add_value(hkey, hval_split[1], 'nan', 'H')
				else:
					self.stats[timestamp][key] = value
					if Results.is_gauge(key):
						kind = 'G'
					else:
						kind = 'C'
					self.add_value(key, value, ' No recorded values', kind)
		self.files.add(fname)
		self.series_count += 1
		self.metrics_count = len(self.matrix.values())
		return True
	
	def process_last_series(self):
		items = sorted(self.results.items())
		# First split items which are not equal anymore
		for key, result in items:			
			result.verify_equaled_out()
		# Create equal groups
		for key, result in items:			
			if not result.discard():
				for key2, result2 in items:
					if key2 == key:
						break
					if result2.discard():
						continue
					if result.verify_is_equal(result2):
						break
			
		self.equaled_out = 0
		self.empty = 0
		self.unique = 0
		self.zeroed_out = 0
		self.anomalies = 0
		self.anomaly_unequal = 0
		self.anomaly_maxed = 0
		self.anomaly_deviated = 0
		self.anomaly_ml = 0
		self.suspected_anomalies = []
		self.filtered_out = len(self.filtered_out_keys)
		for result in self.results.values():
			result.equals_count = len(result.equals)
			if result.anomalies:
				self.anomalies += 1
				if result.anomaly_unequal:
					self.anomaly_unequal += 1
				if result.anomaly_maxed:
					self.anomaly_maxed += 1
					# Adding only maxed to suspected because other kinds are non-important for demo
					self.suspected_anomalies.append(result.name)
					general_logger.info("Suspecting max anomaly in metric %s with diff_max %s and diff_dev %s", result.name, str(result.anomaly_maxed), str(result.anomaly_deviated))
				if result.anomaly_deviated:
					self.anomaly_deviated += 1
				if result.anomaly_ml:
					self.anomaly_ml += 1
			if result.equaled_out:
				self.equaled_out += 1
			elif result.empty:
				self.empty += 1
			elif result.zeroed_out():
				self.zeroed_out += 1
			else:
				self.unique += 1

	def sort_top(self, sort_metric, num_rows, empty_filter):
		self.top = []
		if sort_metric in ['name', 'kind']:
				init_value = ''
		else:
				init_value = -1
		for i in range(0, num_rows):
				self.top.append((None, init_value))
		for metric, result in self.results.items():
				if result.discard() and not (result.empty and not empty_filter):
						continue
				value = result.get(sort_metric)
				for i in range(0, num_rows):
						if value != None and value > self.top[i][1]:
								self.top.insert(i, (metric, value))
								del self.top[-1]
								break


	def process_pod(self, files, file_series, pods_count, warming_up = False):
		for f in files:
			if isfile(join(self.path, f)) and f.startswith(self.name) and (f not in self.files or warming_up):
				if self.read_envoy_data(f, file_series, pods_count):
					general_logger.info("Processing pod file %s", f)
					self.process_last_series()
					self.processed_files += 1
				# break #Uncomment this break to process each existing series per second

class Monitor:
	
	def __init__(self, screen, args):
		global monitor

		self.screen = screen
		self.args = args
		self.pods = {}
		self.refpods = {}
		self.file_series = {}
		self.sort_column = 'eq'
		self.sort_metric = 'equals_count'
		self.current_pod = ''
		self.empty_filter = True
		self.ref_file = ''
		self.current_timestamp = ''
		self.start_timestamp = ''
		self.ref_timestamp = ''
		self.series_count = 0
		self.ml_anomalies = ''
		self.suspected_anomalies = []
		self.global_matrix = {}
		self.reported_anomalies = {}
		Pod.path = args.path
		for pod_name in self.args.pods:
			self.pods[pod_name] = Pod(pod_name, args.path)

		monitor = self

	def adjust_matrix(self):
		val_count = 0
		for key, vals in self.global_matrix.items():
			if val_count == 0 or len(vals) < val_count:
				general_logger.info("Matrix minimal metric is %s with %s", key, str(len(vals)))
				val_count = len(vals)
		logged = False
		for key, vals in self.global_matrix.items():
			while len(vals) > val_count:
				if not logged:
					general_logger.info("Matrix is uneven: key %s has %s with %s for others, adjusting", key, str(len(vals)), val_count)
					logged = True
				vals.pop()
		self.series_count = val_count
	
	def prepare_file_series(self, path, pod_names):
		files = [f for f in os.listdir(path) if "+" in f]
		files = sorted(files, key=lambda x_: x_.split('.')[1])
		self.file_series = {}
		for fname in files:
			pod_name, timestamp = fname.split('.')
			filter_out = True
			for filter_name in pod_names:
				if pod_name.startswith(filter_name):
					filter_out = False
					break
			if filter_out:
				continue
			fseries = self.file_series.get(timestamp)
			if not fseries:
				self.file_series[timestamp] = []
			# Skipping files after saved ref during startup
			if ((self.ref_timestamp == '' or timestamp <= self.ref_timestamp) or
				(self.start_timestamp != '' and timestamp > self.start_timestamp)):
				self.file_series[timestamp].append(fname)
			if timestamp > self.current_timestamp:
				self.current_timestamp = timestamp
		return files

	def process_pods(self, path, pod_names, warming_up = False):
		global learning
		self.pods_count = len(pod_names)
		files = self.prepare_file_series(path, pod_names)
		self.suspected_anomalies = []
		for pod_name in pod_names:
			pod = self.pods.get(pod_name)
			pod.process_pod(files, self.file_series, self.pods_count, warming_up)
			if warming_up:
				pod.return_to_normal()
			if pod_name.startswith(sibling_prefix):
				suspected_anomalies = []
				for anomaly in pod.suspected_anomalies:
					metric = anomaly.split('|', 1)[1]
					for sibling in siblings:
						full_name = sibling + '|' + metric
						if full_name not in self.suspected_anomalies:
							general_logger.info("Adding sibling metric to check %s ", full_name)
							suspected_anomalies.append(full_name)					
			else:
				suspected_anomalies = pod.suspected_anomalies
				general_logger.info("Adding metrics to check %s ", str(pod.suspected_anomalies))
			self.suspected_anomalies.extend(suspected_anomalies)
				
		
		if learning:
			self.ref_timestamp = self.current_timestamp
		# Make sure that matrix is even and update the ML
		self.adjust_matrix()
		general_logger.info("Updating matrix with %s suspected anomalies %s", str(len(self.suspected_anomalies)), self.suspected_anomalies)
		ml.update_matrix(self.global_matrix, self.suspected_anomalies)

		self.pods[self.current_pod].sort_top(self.sort_metric, 20, self.empty_filter)
		self.display_screen(self.pods[self.current_pod], 20)

	def save_pods(self):
		general_logger.info("Saving ref file to %s with timestamp %s", self.ref_file, self.ref_timestamp)
		for pod in self.pods.values():
			pod.matrix = {}
			pod.stats = {}
		with open(self.ref_file, 'wb') as output:
			pickle.dump((self.ref_timestamp, self.pods), output, pickle.HIGHEST_PROTOCOL)
		self.start_timestamp = self.ref_timestamp

	def load_pods(self):
		global learning
		general_logger.info("Loading pods from %s", self.ref_file)
		if isfile(join(self.ref_file)):
			with open(self.ref_file, 'rb') as instream:
				self.ref_timestamp, self.pods = pickle.load(instream)
				general_logger.info("Loaded %s pods with timestamp %s", str(len(self.pods)), self.ref_timestamp)
			learning = False
			ml.processing = True

	def display_top_table(self, pod, num_rows):
		top_table = []
		n = 0
		for metric, value in pod.top:
			if n == num_rows:
				break
			if not metric:
				continue
			top_table.append(pod.results[metric].tabulate_values())
			n += 1
		titles = deepcopy(Results.cols)
		titles[titles.index(self.sort_column)] = self.sort_column.upper()
		self.screen.addstr(tabulate(top_table, headers=titles, tablefmt="plain", floatfmt=".2f"))

	def display_pods_summary(self):
		self.screen.addstr('Pods (use up and down arrows to shift focus of pods):\n')
		for pod in self.pods.values():
			name = pod.name
			if name == self.current_pod:
				name = name.upper()
			self.screen.addstr("    " + name.ljust(20) + "Node: " + pod.node + ', Anomalies: ' + str(pod.anomalies) + ', Unequal: ' + str(pod.anomaly_unequal) +
						', Maxed: ' + str(pod.anomaly_maxed) + ', Deviated: ' + str(pod.anomaly_deviated) + ', ML: ' + str(pod.anomaly_ml) + '\n')
	
	def highlight(self, arr, key):
		arr[arr.index(key)] = key.upper()
		s = ', '
		return s.join(arr)
	
	def display_matrix(self, pod):
		top_table = []
		for item in pod.matrix.matrix.items():
			result = pod.results[item[0]]
			if result.min == 0 or result.max == 0:
				continue
			top_table.append([item[0]] + item[1])
		self.screen.addstr(tabulate(top_table, tablefmt="orgtbl"))

	def draw_graphs(self):
		current_pod = self.pods[self.current_pod]
		anomaly_graph.draw_graphs(current_pod.matrix, current_pod.name, [i[0] for i in current_pod.top])

	def display_screen(self, pod, num_rows):
		self.screen.clear()
		self.screen.addstr('Keys: "q" - exit, "l" - learning/monitoring, "e" - empty on/off, "s" - save, "d" - draw all on/off, arrows left/right - shift sorting\n')
		self.screen.addstr(str(datetime.datetime.now()) + ' Learning: ' + str(learning) + ' ML: ' + str(len(ml.anomalies_found)) + ' progress: ' + ml.progress +
			' processing: ' + ml.processed_column + '\n')
		self.display_pods_summary()
		if pod:
			self.screen.addstr('Pods: ' + str(len(self.pods)) + ' Metrics: ' + str(pod.metrics_count) + ' Series: ' + str(pod.series_count) +
						' Unique: ' + str(pod.unique) + ' Empty: ' + str(pod.empty) +
						' Filtered out: ' + str(pod.filtered_out) + ' Equaled out: ' + str(pod.equaled_out) + 
						' Zeroed out: ' + str(pod.zeroed_out) + '\n')
			#self.display_matrix(pod)
			self.display_top_table(pod, num_rows)
		else:
			self.screen.addstr('Processing')
		self.screen.addstr('\n' + ml.anomaly_info)
		self.screen.refresh()
	
	def shift_index(self, key, shift, arr):
		i = arr.index(key)
		new_i = i + shift
		if new_i < 0 or new_i == len(arr):
			new_i = i
		return new_i
		
	def shift_sort(self, shift):
		self.sort_column = Results.cols[self.shift_index(self.sort_column, shift, Results.cols)]
		self.sort_metric = Results.cols_props[self.sort_column]
	
	def change_pod(self, shift):
		pods_arr = [*self.pods]
		self.current_pod = pods_arr[self.shift_index(self.current_pod, shift, pods_arr)]

	def warm_up(self):
		if self.args.reffile:
			self.ref_file = self.args.reffile
			self.load_pods()
		self.current_pod = self.args.pods[0]		
		general_logger.info("Starting ML thread(s)")
		# The following block is for background processing of selected metrics
		#if self.args.multithreading:
		#	column_filters = self.args.pods
		#else:
		#	column_filters = ['']
		#ml_threads = {}
		#for filter in column_filters:
		#	ml_threads[filter] = threading.Thread(target=process_ml, args=(filter,))
		#	ml_threads[filter].daemon = True
		#	ml_threads[filter].start()

		# Now we use instead the block for confirming suspected metrics
		ml_thread = threading.Thread(target=confirm_anomalies)
		ml_thread.daemon = True
		ml_thread.start()
				
		self.process_pods(self.args.path, self.args.pods, True)
		self.start_timestamp = self.current_timestamp
		if not self.start_timestamp:
			self.start_timestamp = subprocess.check_output("date -Iseconds", shell=True, universal_newlines=True).strip()

	def run(self):
		global learning
		general_logger.info("Running monitoring")
		self.screen.keypad(True)
		self.screen.nodelay(1)
		self.screen.addstr("Processing pods\n")
		self.screen.refresh()
		key = -1
		self.warm_up()
		self.display_screen(None, 20)
		while key != ord('q'):
			key = -1
			time.sleep(DISPLAY_REFRESH_FREQUENCY)
			self.process_pods(self.args.path, self.args.pods)
			while True:
				key = self.screen.getch()
				if key == curses.KEY_LEFT:
					self.shift_sort(-1)
				if key == curses.KEY_RIGHT:
					self.shift_sort(1)
				if key == curses.KEY_UP:
					self.change_pod(-1)
				if key == curses.KEY_DOWN:
					self.change_pod(1)
				if key == ord('e'):
					self.empty_filter = not self.empty_filter
				if key == ord('s'):
					self.screen.addstr('\nSaving to ' + self.args.reffile + '\n')
					self.screen.refresh()
					self.save_pods()
				if key == ord('l'):
					learning = not learning
					ml.processing = not learning
					for pod in self.pods.values():
						pod.set_reference()
				if key == ord('g'):
					self.draw_graphs()
				if key == ord('d'):
					ml.draw_all = not ml.draw_all
				self.screen.refresh()
				if key == -1 or key == ord('q'):
					break
		exit(0)

# Emulation class to use instead of curses in IDE
class Screen:
	def addstr(self, s):
		print(s)
	
	def getch(self):
		return ord('g')
	
	def clear(self):
		pass
	
	def refresh(self):
		pass
	
	def keypad(self, enable):
		pass
	
	def nodelay(self, delay):
		pass

class Servant:
	def __init__(self, monitor_):
		self.monitor = monitor_

	def save(self, json_):
		general_logger.error('Save the pods')
		self.monitor.save_pods()

		return True

	def toggle_learning(self, json_):
		global learning

		learning = not learning
		ml.processing = not learning
		for pod in self.monitor.pods.values():
			pod.set_reference()
		if learning == False:
			self.monitor.save_pods()
		return True

	def quit(self, json_):
		return False

	def _set_value(self, request_, value_):
		if 'promise' not in request_:
			general_logger.error("There is no promise element. Don't know where to store the result")
			return False

		f = open(request_["promise"], "w")
		try:
			f.write(json.dumps(value_))
		finally:
			f.close()

		return True

	def is_learning(self, json_):
		global learning

		return self._set_value(json_, {"learning": learning})

	def query_load(self, json_):
		series_info = {"ref": 0, "total": self.monitor.series_count}

		anomalies_info = []
		for p in filter(lambda p_: 0 < p_.anomaly_maxed, self.monitor.pods.values()):
			anomalies_info.append({"name": p.full_name, "ordinary": p.anomaly_maxed, "ml_confirmed": p.anomaly_ml})

		p = [{"name": v.full_name, "ordinary": v.anomaly_maxed, "ml_confirmed": v.anomaly_ml} for v in self.monitor.pods.values()]
		return self._set_value(json_, {"pods": p, "anomalies": anomalies_info, "samples": series_info})

	def reset_pod_service(self, json_):
		if "pod" not in json_:
			logging.error("There is no pod element")
			return False

		n = str(json_["pod"])
		for p in filter(lambda p_: n == p_.full_name, self.monitor.pods.values()):
			p.return_to_normal()
			return True

		logging.error("Unknown pod %s", n)
		return True

	def reset_anomalies(self, json_):
		for p in filter(lambda p_: 0 < p_.anomaly_maxed, self.monitor.pods.values()):
			p.return_to_normal()

		return True

	def prepare_anomaly_to_report(self, name, anomaly_info, is_sibling=False):
		anomaly_info = deepcopy(anomaly_info)
		p = self.monitor.pods[anomaly_info['pod']]
		anomaly_info['pod'] = p.full_name
		# Cleaning up ranges and positions for siblings of an incident
		if is_sibling:
			for position in anomaly_info['positions'].keys():
				anomaly_info['positions'][position] = []
			for range in anomaly_info['ranges'].keys():
				anomaly_info['ranges'][range] = []
		return anomaly_info

	def query_anomalies_info(self, json_):
		current_anomalies = deepcopy(ml.anomalies_found)
		current_all = deepcopy(ml.normals_found)
		current_all.update(current_anomalies)
		anomalies_to_report = {}
		for key, val in current_anomalies.items():
			if key not in self.monitor.reported_anomalies:
				# Check that non-guilty siblings are not marked as anomalied by ML because of low peaks
				if key.startswith(sibling_prefix) and key not in self.monitor.pods[val['pod']].suspected_anomalies:
					general_logger.info("Skipping reporting of sibling as primary incident %s", key)
					continue
				self.monitor.reported_anomalies[key] = val
				anomalies_to_report[key] = self.prepare_anomaly_to_report(key, val, False)
				general_logger.info("Reporting anomaly %s", key)
				if key.startswith(sibling_prefix):
					metric = key.split('|', 1)[1]
					for sibling in siblings:
						full_name = sibling + '|' + metric
						if full_name != key and full_name in current_all:
							general_logger.info("Reporting sibling anomaly %s", full_name)
							anomalies_to_report[full_name] = self.prepare_anomaly_to_report(full_name, current_all[full_name], True)
			else:
				general_logger.info("Skipping anomaly %s", key)

		for key in list(self.monitor.reported_anomalies.keys()):
			if key not in current_anomalies:
				general_logger.info("Deleting reported anomaly %s", key)
				del self.monitor.reported_anomalies[key]
		general_logger.info("Anomalies to report %s", str(anomalies_to_report))
		return self._set_value(json_, anomalies_to_report)


class Background(Monitor):
	def __init__(self, args_):
		super().__init__(Screen(), args_)
		self.sort_column = 'anom'
		self.sort_metric = 'anomalies'

	def _loop(self, quit_):
		try:
			while not quit_.wait(DISPLAY_REFRESH_FREQUENCY):
				self.process_pods(self.args.path, self.args.pods)
		except Exception as e:
			logging.error(e, exc_info=True)
			raise

	def _despatch(self, json_):
		if not json_:
			general_logger.error('Invalid JSON command object')
			return False

		if 'command' not in json_:
			general_logger.error('There is no command element')
			return False

		c = json_['command']
		o = getattr(Servant(self), c, None)
		if not callable(o):
			logging.error('Bad command name "%s"', c)
			return False

		return o(json_)

	def display_screen(self, pod, num_rows):
		pass

	def run(self):
		general_logger.info("Running monitoring")
		super().warm_up()

		E = threading.Event()
		w = threading.Thread(target=self._loop, args=[E])
		w.start()

		B = []
		G = True
		H = sys.stdin.fileno()
		F = fcntl.fcntl(H, fcntl.F_GETFL)
		fcntl.fcntl(H, fcntl.F_SETFL, F | os.O_NONBLOCK)

		while G:
			p = select.poll()
			p.register(H, select.POLLIN | select.POLLPRI | select.POLLHUP | select.POLLERR)
			b = None
			try:
				for h, f in p.poll():
					if f & (select.POLLIN | select.POLLPRI):
						b = os.read(h, 1024)
					else:
						break
			except KeyboardInterrupt:
				pass

			if not b or len(b) == 0:
				break

			# TODO: need a check for the buffer is too large say more than 1024
			B.extend(b)
			while True:
				p = None
				try:
					p = B.index(0)
				except ValueError:
					break

				Q = None
				try:
					Q = json.loads(bytearray(B[0:p]).decode())
				except json.decoder.JSONDecodeError:
					pass

				if not self._despatch(Q):
					G = False
					break

				del B[0:p + 1]

		E.set()
		w.join()

def main():
	parser = argparse.ArgumentParser()
	parser.add_argument('path', help='metrics dir')
	parser.add_argument('-r', '--reffile', help='reference model file')
	parser.add_argument('-p', '--pods', help='list of pods', nargs='+')
	parser.add_argument('-B', '--background', help='enables background mode', action='store_true')
	parser.add_argument('-m', '--multithreading', action='store_true', help='multithreading ml')
	args = parser.parse_args()
	if args.background:
		Background(args).run()
	else:
		# Use this wrapper to run in top-like mode
		wrapper(lambda x_, y_: Monitor(x_, y_).run(), args)

main()
