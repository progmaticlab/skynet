#!/usr/bin/python3

import argparse
import curses
import datetime
import math
import os
import pickle
import re
import time
from copy import deepcopy
from curses import wrapper
from os.path import isfile, join
from tabulate import tabulate
from platform import node

EQUAL_ROWS_THRESHOLD = 0.05
ANOMALY_MAX_THRESHOLD = 0.05
ANOMALY_DEVIATION_THRESHOLD = 0.05

learning = True

exclude_keys = ['version', 'istio', 'prometheus', 'grafana', 'nginx', 'kube', 'jaeger', 'BlackHole', 'grpc', 'zipkin', 'mixer']

def exclude_row(key, value):
	for exclude in exclude_keys:
		if exclude in key:
			return True
	#if not '9080' in key:
	#	return True
	return False

class Results:
	cols = ['name', 'nature', 'equals', 'anomalies', 'min', 'avg', 'max', 'dev', 'navg', 'ndev', 'val', 'nval', 'd_equals', 'd_max', 'd_dev', 'd_ndev', 'start']
	cols_props = {'name': 'name', 'nature': 'nature', 'start': 'start',
				'equals': 'equals_count', 'min': 'min', 'avg': 'avg', 'max': 'max', 'dev': 'dev',
				'navg': 'norm_avg', 'ndev': 'norm_dev', 'val': 'last_value', 'nval': 'norm_last_value',
				'd_equals': 'diff_equals_count', 'd_max': 'diff_max', 'd_dev': 'diff_dev', 'd_ndev': 'diff_norm_dev',
				'anomalies': 'anomalies'}
	
	def __init__(self, name, nature):
		# Name: name of the metric
		self.name = name
		# Nature: 'histo', 'counter', 'gauge' - counters are growing
		self.nature = nature
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


	def get(self, prop):
		return getattr(self, prop)
	
	def discard(self):
		return self.empty or self.filtered_out or self.equaled_out or self.zeroed_out()
	
	def zeroed_out(self):
		return self.min == float('inf') or self.max == 0

	def tabulate_values(self):
		return [self.name, self.nature, self.equals_count, self.anomalies,
				self.min, self.avg, self.max, self.dev,
				self.norm_avg, self.norm_dev, self.last_value, self.norm_last_value,
				self.diff_equals_count, self.diff_max, self.diff_dev, self.diff_norm_dev, self.start]
		
	def is_equal(self, result):
		return (abs(self.norm_last_value - result.norm_last_value) <= EQUAL_ROWS_THRESHOLD and
				abs(self.norm_avg - result.norm_avg) <= EQUAL_ROWS_THRESHOLD)

	def normalize(self, value):
		return value / self.max
	
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
		if self.nature == 'counter':
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
			if self.diff_max > self.ref_max * (1 + ANOMALY_MAX_THRESHOLD):
				self.anomaly_max = self.diff_max
				self.anomalies += 1
			if self.diff_norm_dev > ANOMALY_DEVIATION_THRESHOLD:
				self.anomaly_norm_deviated = self.diff_norm_dev
				self.anomalies += 1


class Pod:
	path = ''
	pods_info = {}

	def __init__(self, name, path):
		Pod.path = path
		self.name = name
		self.full_name = ''
		self.node = ''
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

	def set_reference(self):
		for result in self.results.values():
			result.set_reference()

	def add_value(self, key, value, empty, nature):
		if not key in self.results:
			result = Results(key, nature) 
			self.results[key] = result
		else:
			result = self.results[key]
		if not key in self.matrix:
			self.matrix[key] = []
		if value == empty:
			value = ''
		self.matrix[key].append(value)
		result.process_value(value)

	def shorten(self, key):
		key = key.replace('cluster.inbound', 'c.in')
		key = key.replace('cluster.outbound', 'c.out')
		key = key.replace('default.svc.cluster.local', 'd.s.c.l')
		return key

	@classmethod
	def get_node(cls, timestamp, pod):
		if not cls.pods_info.get(timestamp):
			Pod.pods_info[timestamp] = {}
			with open(join(Pod.path, 'pods.' + timestamp)) as f:
				fcontents = f.read()
				contents = fcontents.splitlines()
				it = iter(contents)
				for row in it:
					pod = row.split(':')[1].lstrip()
					node = next(it).split(':')[1].lstrip()
					Pod.pods_info[timestamp][pod] = node
		return Pod.pods_info[timestamp][pod]
		
	def read_envoy_data(self, fname):
		with open(join(self.path, fname), 'r') as f:
			fcontents = f.read()
			contents = fcontents.splitlines()
			pod_name, timestamp = fname.split('.')
			self.full_name = pod_name
			if not timestamp in self.stats:
				self.stats[timestamp] = {}
				self.node = Pod.get_node(timestamp, self.full_name)
			for row in contents:
				row_split = row.split(':')
				try:
					key = self.shorten(row_split[0])
					value = row_split[1]
				except:
					print(fname, row)
				if exclude_row(key, value):
					self.filtered_out_keys.add(key)
					continue
				if 'P0(' in value:
					histogram = value.split()
					for hval in histogram:
						hval_split = re.split('[(,)]', hval)
						if hval_split[0] in ['P0', 'P50', 'P100']:
							hkey = key + '|' + hval_split[0]
							self.stats[timestamp][key] = hval_split[1]
							self.add_value(hkey, hval_split[1], 'nan', 'histo')
				else:
					self.stats[timestamp][key] = value
					if key.endswith('active') or key.endswith('buffered'):
						nature = 'gauge'
					else:
						nature = 'counter'
					self.add_value(key, value, ' No recorded values', nature)
		self.files.add(fname)
		self.series_count += 1
		self.metrics_count = len(self.matrix.values())
	
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
		self.filtered_out = len(self.filtered_out_keys)
		for result in self.results.values():
			result.equals_count = len(result.equals)
			if result.anomalies:
				self.anomalies += 1
				if result.anomaly_unequal:
					self.anomaly_unequal += 1
				if result.anomaly_maxed:
					self.anomaly_maxed += 1
				if result.anomaly_deviated:
					self.anomaly_deviated += 1
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
		if sort_metric in ['name', 'nature']:
				init_value = ''
		else:
				init_value = -1
		for i in range(0, num_rows):
				self.top.append((None, init_value))
		for metric, result in self.results.items():
				if result.discard() and not empty_filter and not result.empty:
						continue
				value = result.get(sort_metric)
				for i in range(0, num_rows):
						if value != None and value > self.top[i][1]:
								self.top.insert(i, (metric, value))
								del self.top[-1]
								break


	def process_pod(self, files):
		for f in files:
			if isfile(join(self.path, f)) and f.startswith(self.name) and not f in self.files:
				#if self.series_count == 107:
				#	quit()
				self.read_envoy_data(f)
				self.process_last_series()
				# break #Uncomment this break to process each existing series per second

class Monitor:
	
	def __init__(self, screen, args):
		self.screen = screen
		self.args = args
		self.pods = {}
		self.refpods = {}
		self.sort_column = 'equals'
		self.sort_metric = 'equals_count'
		self.current_pod = ''
		self.empty_filter = True
		self.ref_file = ''

	def process_pods(self, path, pod_names):
		files = os.listdir(path)
		files.sort()
		for pod_name in pod_names:
			pod = self.pods.get(pod_name)
			if pod == None:
				pod = Pod(pod_name, path)
				self.pods[pod_name] = pod
				self.display_screen(None, 20)			
			pod.process_pod(files)
		self.pods[self.current_pod].sort_top(self.sort_metric, 20, self.empty_filter)
		self.display_screen(self.pods[self.current_pod], 20)

	def save_pods(self):
		for pod in self.pods.values():
			pod.matrix = {}
			pod.stats = {}
		with open(self.ref_file, 'wb') as output:
			pickle.dump(self.pods, output, pickle.HIGHEST_PROTOCOL)

	def load_pods(self):
		if isfile(join(self.ref_file)):
			with open(self.ref_file, 'rb') as instream:
				self.pods = pickle.load(instream)

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
						', Maxed: ' + str(pod.anomaly_maxed) + ', Deviated: ' + str(pod.anomaly_deviated) + '\n')
	
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
	
	def display_screen(self, pod, num_rows):
		self.screen.clear()
		self.screen.addstr('Keys: "q" - exit, "l" - toggle learning/monitoring, "e" - toggle empty, "s" - save, arrows left/right - shift sorting\n')
		self.screen.addstr(str(datetime.datetime.now()) + ' Learning: ' + str(learning) + '\n')
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

	def run(self):
		self.screen.keypad(True)
		self.screen.nodelay(1)
		self.screen.addstr("Processing pods\n")
		self.screen.refresh()
		if self.args.reffile:
			self.ref_file = self.args.reffile
			self.load_pods()
		self.current_pod = self.args.pods[0]		
		key = -1
		while key != ord('q'):
			key = -1
			time.sleep(1)
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
					for pod in self.pods.values():
						pod.set_reference()
				self.screen.refresh()
				if key == -1 or key == ord('q'):
					break

# Emulation class to use instead of curses in IDE
class Screen:
	def addstr(self, s):
		print(s)
	
	def getch(self):
		return -1 #raw_input()
	
	def clear(self):
		pass
	
	def refresh(self):
		pass
	
	def keypad(self, enable):
		pass
	
	def nodelay(self, delay):
		pass

def run(stdscr, args):
	monitor = Monitor(stdscr, args)
	monitor.run()

def main():
	parser = argparse.ArgumentParser()
	parser.add_argument('path', help='metrics dir')
	parser.add_argument('-r', '--reffile', help='reference model file')
	parser.add_argument('-p', '--pods', help='list of pods', nargs='+')
	args = parser.parse_args()
	# Use this wrapper to run in top-like mode
	wrapper(run, args)
	# Use direct run for console sequential output for debugging purposes
	#run(Screen(), args)

main()
