import argparse
import copy
import csv
import curses
import datetime
import math
import os
import re
import sys
import time
from curses import wrapper
from os.path import isfile, join
from tabulate import tabulate
from __builtin__ import str
from pip._vendor.html5lib.filters.sanitizer import allowed_protocols
from copy import deepcopy

EQUAL_ROWS_THRESHOLD = 0.05

pods = {}
refpods = {}
screen = None
sort_column = 'equals'
sort_metric = 'equals_count'
current_pod = ''
empty_filter = True
learning = True

def exclude_row(key, value):
	if not '9080' in key or 'version' in key:
		return True
	return False

class Results:
	cols = ['name', 'nature', 'equals', 'min', 'avg', 'max', 'dev', 'navg', 'ndev', 'val', 'nval', 'd_equals', 'd_dev', 'd_ndev', 'start']
	cols_props = {'name': 'name', 'nature': 'nature', 'start': 'start',
				'equals': 'equals_count', 'min': 'min', 'avg': 'avg', 'max': 'max', 'dev': 'dev',
				'navg': 'norm_avg', 'ndev': 'norm_dev', 'val': 'last_value', 'nval': 'norm_last_value',
				'd_equals': 'diff_equals_count', 'd_dev': 'diff_dev', 'd_ndev': 'diff_norm_dev'}
	
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
		# Old primary equal: to correctly split groups
		self.old_primary_equal = None
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
		# Ref deviation: reference deviation
		self.ref_dev = 0.0
		# Ref normalized deviation: reference normalize deviation
		self.ref_norm_dev = 0.0
		# Equals count diff: difference between current and reference equals count
		self.diff_equals_count = 0
		# Deviation diff: difference between current and reference deviations
		self.diff_dev = 0.0
		# Deviation diff: difference between current and reference normalized deviations

	def get(self, prop):
		return getattr(self, prop)
	
	def discard(self):
		return self.empty or self.filtered_out or self.primary_equal or self.zeroed_out()
	
	def zeroed_out(self):
		return self.min == float('inf') or self.max == 0

	def tabulate_values(self):
		return [self.name, self.nature, self.equals_count, self.min, self.avg, self.max, self.dev,
				self.norm_avg, self.norm_dev, self.last_value, self.norm_last_value,
				self.diff_equals_count, self.diff_dev, self.diff_norm_dev, self.start]
		
	def is_equal(self, result):
		return (not result.empty and abs(self.norm_last_value - result.norm_last_value) <= EQUAL_ROWS_THRESHOLD and
				abs(self.norm_avg - result.norm_avg) <= EQUAL_ROWS_THRESHOLD and
				(self.old_primary_equal == None or result.old_primary_equal == None or
				self.old_primary_equal.name == result.old_primary_equal.name))

	def normalize(self, value):
		return value / self.max
	
	def set_reference(self):
		self.ref_count = self.count
		self.ref_equals_count = self.equals_count
		self.ref_dev = self.dev
		self.ref_norm_dev = self.norm_dev
		self.diff_equals_count = 0
		self.diff_dev = 0.0
		self.diff_norm_dev = 0.0

	# Adds another "slave" sibling to a group of equals
	def set_equal(self, new_primary_equal):
		self.old_primary_equal = self.primary_equal
		if self.old_primary_equal:
			self.old_primary_equal.equals.remove(self.name)
		self.primary_equal = new_primary_equal
		if new_primary_equal:
			new_primary_equal.equals.add(self.name)

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
			self.diff_dev = 0.0
			self.diff_norm_dev = 0.0
		else:
			# We'll be looking for metrics with less equals than in reference, which means less uniformity
			self.diff_equals_count = self.ref_equals_count - self.equals_count
			# We'll be looking for metrics with increased deviations
			self.diff_dev = self.dev - self.ref_dev
			self.diff_norm_dev = self.norm_dev - self.ref_norm_dev


class Pod:
	def __init__(self, name, path):
		self.name = name
		self.path = path
		self.stats = {}
		self.results = {}
		self.ref_results = None
		self.matrix = {}
		self.files = set()
		self.series_count = 0
		self.metrics_count = 0
		self.top = []
		self.unique = 0
		self.empty = 0
		self.filtered_out = 0
		self.equaled_out = 0
		self.zeroed_out = 0

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

	def read_envoy_data(self, fname):
		with open(join(self.path, fname), 'r') as f:
			fcontents = f.read()
			contents = fcontents.splitlines()
			timestamp = fname.split('.')[1]
			if not timestamp in self.stats:
				self.stats[timestamp] = {}
			for row in contents:
				row_split = row.split(':')
				try:
					key = self.shorten(row_split[0])
					value = row_split[1]
				except:
					print fname, row
				if exclude_row(key, value):
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
		self.empty = 0
		self.filtered_out = 0
		self.equaled_out = 0
		for key, result in items:			
			if result.filtered_out or result.empty or result.zeroed_out():
				continue
			
			if result.primary_equal and not result.is_equal(self.results[result.primary_equal.name]):
				result.set_equal(None)
			
			if not result.primary_equal:
				for key2, result2 in items:
					if key2 == key:
						break
					if result2.discard():
						continue

					if result.is_equal(result2):
						result.set_equal(result2)
			
		self.equaled_out = 0
		self.empty = 0
		self.filtered_out = 0
		self.unique = 0
		self.zeroed_out = 0
		for result in self.results.values():
			result.equals_count = len(result.equals)
			if result.primary_equal:
				self.equaled_out += 1
			elif result.filtered_out:
				self.filtered_out += 1
			elif result.empty:
				self.empty += 1
			elif result.zeroed_out():
				self.zeroed_out += 1
			else:
				self.unique += 1

	def sort_top(self, sort_metric, num_rows):
		self.top = []
		for i in range(0, num_rows):
			self.top.append((None, -1))
		for metric, result in self.results.iteritems():
			if result.filtered_out or result.primary_equal or result.zeroed_out() or (empty_filter and result.empty):
				continue
			value = result.get(sort_metric)
			for i in range(0, num_rows):
				if value > self.top[i][1]:
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
				break	

def process_pods(path, pod_names):
		files = os.listdir(path)
		files.sort()
		for pod_name in pod_names:
			pod = pods.get(pod_name)
			if pod == None:
				pod = Pod(pod_name, path)
				pods[pod_name] = pod
			pod.process_pod(files)
		pods.values()[0].sort_top(sort_metric, 20)
		display_screen(pods.values()[0], 20)

def display_top_table(pod, num_rows):
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
	titles[titles.index(sort_column)] = sort_column.upper()
	screen.addstr(tabulate(top_table, headers=titles, tablefmt="plain", floatfmt=".2f"))

def highlight(arr, key):
	highlighted = deepcopy(arr)
	highlighted[arr.index(key)] = key.upper()
	return str(join(highlighted))

def display_matrix(pod):
	top_table = []
	for item in pod.matrix.matrix.items():
		result = pod.results[item[0]]
		if result.min == 0 or result.max == 0:
			continue
		top_table.append([item[0]] + item[1])
	screen.addstr(tabulate(top_table, tablefmt="orgtbl"))

def display_screen(pod, num_rows):
	screen.clear()
	screen.addstr('Keys: "q" - exit, "l" - toggle learning/monitoring, "e" - toggle empty, arrows left/right - shift sorting\n')
	screen.addstr(str(datetime.datetime.now()) + ' Learning: ' + str(learning) + '\n')
	screen.addstr('Pods: ' + highlight(pods.keys(), current_pod) + ' press up or down to change pods\n')
	screen.addstr('Pods: ' + str(len(pods)) + ' Metrics: ' + str(pods.values()[0].metrics_count) + ' Series: ' + str(pods.values()[0].series_count) +
					' Unique: ' + str(pod.unique) + ' Empty: ' + str(pod.empty) +
					' Filtered out: ' + str(pod.filtered_out) + ' Equaled out: ' + str(pod.equaled_out) + 
					' Zeroed out: ' + str(pod.zeroed_out) + '\n')
	
	#display_matrix(pod)
	display_top_table(pod, num_rows)
	screen.refresh()

def shift_index(key, shift, arr):
	i = arr.index(key)
	new_i = i + shift
	if new_i < 0 or new_i == len(arr):
		new_i = i
	return new_i
	
def shift_sort(shift):
	global sort_column, sort_metric
	sort_column = Results.cols[shift_index(sort_column, shift, Results.cols)]
	sort_metric = Results.cols_props[sort_column]

def change_pod(shift):
	global current_pod, pods
	current_pod = pods.keys()[shift_index(current_pod, shift, pods.keys())]
	
# Emulation class to use instead of curses in IDE
class Screen:
	def addstr(self, str):
		print str
	
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

key = -1

def main(stdscr):
	global screen, empty_filter, learning, key, current_pod
	screen = stdscr
	parser = argparse.ArgumentParser()
	parser.add_argument('path', help='metrics dir')
	parser.add_argument('-r', '--refpath', help='reference model metrics dir')
	parser.add_argument('-p', '--pods', help='list of pods', nargs='+')
	args = parser.parse_args()

	stdscr.keypad(True)
	stdscr.nodelay(1)
	stdscr.addstr("Processing pods\n")
	current_pod = args.pods[0]
	key = -1
	while key != ord('q'):
		key = -1
		time.sleep(1)
		process_pods(args.path, args.pods)
		while True:
			key = stdscr.getch()
			if key == curses.KEY_LEFT:
				shift_sort(-1)
			if key == curses.KEY_RIGHT:
				shift_sort(1)
			if key == curses.KEY_UP:
				change_pod(-1)
			if key == curses.KEY_DOWN:
				change_pod(1)
			if key == ord('e'):
				empty_filter = not empty_filter
			if key == ord('l'):
				learning = not learning
				for pod in pods.values():
					pod.set_reference()
			stdscr.refresh()
			if key == -1 or key == ord('q'):
				break

wrapper(main)
#main(Screen())