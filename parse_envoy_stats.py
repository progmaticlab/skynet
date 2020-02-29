import argparse
import copy
import csv
import os
import re
import sys
import time
from os.path import isfile, join
from Carbon.Aliases import false
from audioop import avg
from tabulate import tabulate
from matplotlib.font_manager import path

STATS_PERCENTILE = 95
EQUAL_ROWS_THRESHOLD_PERCENTAGE = 5

pods = {}
refpods = {}

def exclude_row(key, value):
	if not '9080' in key or 'version' in key:
		return True
	return False


class Matrix:	
	def __init__(self, pod_results, matrix = None):
		if matrix:
			self.matrix = matrix
		else:
			self.matrix = {}
		self.top = {}
		self.results = pod_results

	def add_value(self, timestamp, key, value, empty, nature):
		if not key in self.results: 
			self.results[key] = {'nature': nature, 'start': ''}
		if not key in self.matrix:
			self.matrix[key] = [key]
		if value != empty:
			if nature == 'counter':
				old_value = self.results[key].get('counter')
				if old_value:
					self.results[key]['counter'] = float(value)
					value = float(value) - old_value
				else:
					self.results[key]['counter'] = float(value)											
					self.results[key]['start'] = float(value)
					value = 0						
			self.matrix[key].append(value)
		else:
			self.matrix[key].append('')

	def normalize_matrix(self):
		nmatrix = copy.deepcopy(self.matrix)
		for row in nmatrix.keys():
			if not self.results[row].get('filtered_out'):
				vmin, vmax, vavg = self.calc_min_max_avg(row)
				self.results[row]['min'] = vmin
				self.results[row]['max'] = vmax
				self.results[row]['avg'] = vavg
				values = nmatrix[row]
				for i in range(1, len(values)):
					if values[i] != '' and vmax != 0:
						values[i] = float(values[i]) / vmax
		return Matrix(self.results, nmatrix)		

	def compare_rows(self, key1, key2):
		values1 = self.matrix[key1]
		values2 = self.matrix[key2]
		vmax = 0
		for i in range(1, len(values1)):
			if values2[i] == '' or values1[i] == '':
				continue
			diff = float(values2[i]) - float(values1[i])
			if abs(diff) > vmax:
				vmax = abs(diff)
		return vmax
		
	def weigh_num_changes(self, row):
		num_changes = 0
		values = self.matrix[row]
		value = -1
		for i in range(1, len(values)):
			if values[i] == '':
				continue
			if value == -1:
				value = values[i]
			elif (values[i] != value):
				num_changes += 1
		return num_changes

	def filter_out_changes(self, row):
		weight = self.weigh_num_changes(row)
		if weight == 0: # or weight > len(matrix[row]) - 2:
			return 0
		else:
			return weight
	
	def calc_min_max_avg(self, row, percentile = 100):
		vsum = 0
		values = sorted(self.matrix[row][1:], key=lambda x: float(x) if x != '' else -1)
		count = 0
		delta = 0
		# Skip initial empty '' values and determine count and delta to account 
		# for percentile and drop values from beginning and at the end
		for i in range(0, len(values)):
			if values[i] == '':
				continue
			if count == 0:
				count = len(values) - i + 1
				delta = count * (100 - percentile) / 100 / 2
				count = count - delta * 2
				start = i + delta
				break
		if count == 0:
			return 0, 0, 0
		for i in range(start, start + count - 1):	
			value = float(values[i])
			vsum += value
		vavg = float(vsum) / count
		return float(values[start]), float(values[start + count - 2]), vavg
	
	def weigh_dispersion(self, row):
		vmin, vmax, vavg = self.calc_min_max_avg(row)
		if vmax == 0:
			return 0
		return int(float(vmax - vmin) * 100 / 2 / vmax )
	
	def weigh_max_peaks(self, row):
		vmin, vmax, vavg = self.calc_min_max_avg(row)
		if vmax == 0:
			return 0
		disp = abs(float(vmax + vmin) / 2 - vavg + float(vmax - vmin) / 2) * 100 / vmax
		if disp == 0:
			return 0
		return int(disp)

	def criterion_dispersion(self, row):
		if self.filter_out(row):
			return 0
		return self.weigh_dispersion(row)
			
	def criterion_max_peaks(self, row):
		if self.filter_out(row):
			return 0
		return self.weigh_max_peaks(row)

	def criterion_num_changes(self, row):
		return self.filter_out_changes(row)
	
	def filter_out(self, row):
		if self.filter_out_changes(row) == 0:
			self.results[row]['filtered_out'] = True
			return True
		return False
	
	def compute_results(self, num_rows, criteria):
		for i in range(0, num_rows):
			for criterion in criteria:
				if not criterion.__name__ in self.top:
					self.top[criterion.__name__] = []
				self.top[criterion.__name__].append(('dummy', -1))
		for row in self.matrix:
			for criterion in criteria:
				weight = criterion(self, row)
				cname = criterion.__name__
				self.results[row][cname] = weight
				for i in range(0, num_rows):
					if weight > self.top[cname][i][1]:
						self.top[cname].insert(i, (row, weight))
						del self.top[cname][-1]
						break	 

class Pod:
	def __init__(self, name, path):
		self.name = name
		self.path = path
		self.stats = {}
		self.results = {}
		self.matrix = Matrix(self.results)
		self.nmatrix = Matrix(self.results)

	def display_all(self, data, csv_name = ''):
		table = []
		matrix = data.matrix
		for row in matrix.keys():
			if not self.results[row].get('filtered_out'):
				table.append([
					self.results[row]['nature'],
					self.results[row]['start'],
					self.results[row]['evenness'],
					self.results[row]['equals_count'],
					self.results[row][Matrix.criterion_num_changes.__name__],
					self.results[row][Matrix.criterion_dispersion.__name__],
					self.results[row][Matrix.criterion_max_peaks.__name__],
					] + matrix[row])
		titles = ['nature', 'start_value', 'evenness', 'equals', 'changes', 'dispersion', 'peaks', 'name'] + sorted(self.stats.iterkeys())
		print tabulate(table, headers=titles, tablefmt="orgtbl")
		
		if csv_name != '':
			self.write_csv(csv_name, titles, table)

	def write_csv(self, csv_name, titles, table):
		with open(csv_name, 'w') as csvfile:
			print "Writing csv file:", csvfile.name
			writer = csv.writer(csvfile)
			writer.writerow(titles)
			writer.writerows(table)

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
					key = row_split[0]
					value = row_split[1]
				except:
					print(fname, row)
				if exclude_row(key, value):
					continue
				if 'P0(' in value:
					histogram = value.split()
					for hval in histogram:
						hval_split = re.split('[(,)]', hval)
						if hval_split[0] in ['P0', 'P50', 'P100']:
							hkey = key + '|' + hval_split[0]
							self.stats[timestamp][hkey] = hval_split[1]
							self.matrix.add_value(timestamp, hkey, hval_split[1], 'nan', 'histo')
				else:
					self.stats[timestamp][key] = value
					if key.endswith('active') or key.endswith('buffered'):
						nature = 'gauge'
					else:
						nature = 'counter'
					self.matrix.add_value(timestamp, key, value, ' No recorded values', nature)

	def analyze_row(self, row_key):
		nmin, nmax, navg = self.nmatrix.calc_min_max_avg(row_key, STATS_PERCENTILE)
		self.results[row_key]['pmin'] = nmin
		self.results[row_key]['pmax'] = nmax
		self.results[row_key]['pavg'] = navg
		if self.results[row_key]['max'] == 0:
			self.results[row_key]['evenness'] = 0
		else:
			self.results[row_key]['evenness'] = int((nmax - nmin) / (self.results[row_key]['max']) * 100)

	def init_results(self):
		for row in self.matrix.matrix:
			if not self.results[row].get('filtered_out'):
				self.results[row]['equals'] = []
				self.results[row]['equals_count'] = 0

	def analyze_results(self):
		nkeys = self.nmatrix.matrix.keys()
		nkeys_len = len(nkeys)
		for i in range(0, nkeys_len):
			key = nkeys[i]
			self.analyze_row(key)
			if self.results[key].get('filtered_out'):
				continue
			for k in range(i + 1, nkeys_len):
				if self.results[nkeys[k]].get('filtered_out'):
					continue
				diff_max = self.nmatrix.compare_rows(nkeys[i], nkeys[k])
				if diff_max <= self.results[key]['pmax'] / 100 * EQUAL_ROWS_THRESHOLD_PERCENTAGE:
					self.results[key]['equals'].append(nkeys[k])
					self.results[nkeys[k]]['equals'].append(key)
					self.results[key]['equals_count'] = len(self.results[key]['equals'])
					self.results[nkeys[k]]['filtered_out'] = True

	def process_pod(self):
		# print "Handling files for pod:", self.name
		files = os.listdir(self.path)
		files.sort()
		for f in files:
			if isfile(join(self.path, f)) and f.startswith(self.name):
				# print f
				self.read_envoy_data(f)
		#transform_prometheus_matrix()
		self.init_results()
		self.nmatrix = self.matrix.normalize_matrix()
		self.matrix.compute_results(len(self.matrix.matrix), [Matrix.criterion_dispersion, Matrix.criterion_max_peaks, Matrix.criterion_num_changes])
		self.analyze_results()
		# print 'Total rows', len(self.matrix.matrix)
		# print ''
		# print 'All data:'
		csv_name = join(self.path, 'csv-' + self.name + '.csv')
		#self.display_all(self.matrix, csv_name)
		csv_name = join(self.path, 'csv-norm-' + self.name + '.csv')
		#self.display_all(self.nmatrix, csv_name )
		#print 'Max peaks:'
		#csv_name = join(self.path, 'csv-' + self.name + '-max_peaks.csv')
		#display_top('max_peaks', csv_name)
		#print ''
		#csv_name = join(self.path, 'csv-' + self.name + '-dispersion.csv')
		#print 'Dispersion:'
		#display_top('dispersion', csv_name)
		#print ''
		#csv_name = join(self.path, 'csv-' + self.name + '-changes.csv')
		#print 'Num changes:'
		#display_top('num_changes', csv_name)
		
def main():
	global stats, matrices, results, top
	parser = argparse.ArgumentParser()
	parser.add_argument('path', help='metrics dir')
	parser.add_argument('-r', '--refpath', help='reference model metrics dir')
	parser.add_argument('-p', '--pods', help='list of pods', nargs='+')
	args = parser.parse_args()

	print "Processing pods"
	if args.refpath:
		# print "Parsing reference model"
		for pod_name in args.pods:
			pod = Pod(pod_name, args.refpath)
			pod.process_pod()
			refpods[pod_name] = pod

	print "Parsing metrics"
	for pod_name in args.pods:
		pod = Pod(pod_name, args.path)
		pod.process_pod()
		pods[pod_name] = pod

	print "Looking for anomalies"
	for pod_name in pods.keys():
		results = pods[pod_name].results
		refresults = refpods[pod_name].results
		for row in results.keys():
			if not results[row].get('filtered_out') and results[row]['evenness'] > refresults[row]['evenness']:
				print "Pod:", pod_name,'Evenness:', results[row]['evenness'], '>', refresults[row]['evenness'], "Row:", row 
			if not results[row].get('filtered_out') and results[row]['equals_count'] < refresults[row]['equals_count']:
				print "Pod:", pod_name, 'Equals:', results[row]['equals_count'], '<', refresults[row]['equals_count'], "Row:", row
	print "Done"

main()
