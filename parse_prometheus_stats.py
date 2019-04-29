import argparse
import csv
import os
import sys
import time
from os.path import isfile, join
from Carbon.Aliases import false
from audioop import avg
from tabulate import tabulate

stats = {}
matrix = {}
results = {}
top = {}
filtered_out = set()

def get_name_key(row):
	if row['name'].startswith('envoy'):
		key1 = row['name']
		key2 = row['pod_name']
		key3 = row['cluster_name']
	elif row['name'].startswith('kube'):
		key1 = row['name']
		key2 = row['pod']
		key3 = row['container']	
	return join(key1, key2, key3), key1, key2, key3

def read_table(path, fname):
	global stats
	with open(join(path, fname)) as csvfile:
		csvreader = csv.DictReader(csvfile)
		for row in csvreader:
			key, key1, key2, key3 = get_name_key(row)
			matrix[key] = [key1, key2, key3]
			timestamp = time.strftime('%d %H:%M:%S', time.localtime(float(row['timestamp'])))
			if not timestamp in stats:
				stats[timestamp] = {}
			if key.startswith('envoy'):
				stats[timestamp][key] = row['value']
			elif (key.startswith('kube_pod_container_status_terminated_reason') or
				key.startswith('kube_pod_container_status_waiting_reason')):
				if row['value'] == '1':
					stats[timestamp][key] = row['reason']
			elif key.startswith('kube_pod_status_phase'):
				if row['value'] == '1':
					stats[timestamp][key] = row['phase']
			elif (key.startswith('kube_pod_status_ready') or
				key.startswith('kube_pod_status_scheduled')):
				if row['value'] == '1':
					stats[timestamp][key] = row['condition']
			elif key.startswith('kube_pod') or key.startswith('kubelet'):
				stats[timestamp][key] = row['value']
			else:
				print "Unresolved key", key
				sys.exit(1)
			

def transform_matrix():
	for series in sorted(stats.iterkeys()):
		for key in matrix.keys():
			if key in stats[series]:
				matrix[key].append(stats[series][key])
			else:
				matrix[key].append('')

def weigh_num_changes(row):
	num_changes = 0
	values = matrix[row]
	value = -1
	for i in range(3, len(values)):
		if values[i] == '':
			continue
		if value == -1:
			value = values[i]
		elif (values[i] != value):
			num_changes += 1
	return num_changes

def filter_out_changes(row):
	weight = weigh_num_changes(row)
	if weight == 0: # or weight > len(matrix[row]) - 2:
		return 0
	else:
		return weight

def criterion_num_changes(row):
	return filter_out_changes(row)

def calc_min_max_avg(row):
	vmin = sys.maxint
	vmax = 0
	vsum = 0
	values = matrix[row]
	count = 0
	for i in range(3, len(values)):
		if values[i] == '':
			continue
		try:
			value = int(values[i])
		except ValueError:
			return 0, 0, 0	
		if value < vmin:
			vmin = value
		if value > vmax:
			vmax = value
		vsum += value
		count += 1
	vavg = float(vsum) / count
	return vmin, vmax, vavg

def weigh_dispersion(row):
	vmin, vmax, vavg = calc_min_max_avg(row)
	if vmax == 0:
		return 0
	return int(float(vmax - vmin) * 100 / 2 / vmax )

def criterion_dispersion(row):
	if filter_out(row):
		return 0
	return weigh_dispersion(row)

def weigh_max_peaks(row):
	vmin, vmax, vavg = calc_min_max_avg(row)
	if vmax == 0:
		return 0
	disp = abs(float(vmax + vmin) / 2 - vavg + float(vmax - vmin) / 2) * 100 / vmax
	if disp == 0:
		return 0
	return int(disp)
	
def criterion_max_peaks(row):
	if filter_out(row):
		return 0
	return weigh_max_peaks(row)
	
def filter_out(row):
	global total_filtered_out
	if (filter_out_changes(row) == 0 or
		'version' in row):
		filtered_out.add(row)
		return True
	return False

def get_top(num_rows, criterion):
	top = []
	for i in range(0, num_rows):
		top.append(('dummy', 0))
	for row in matrix:
		weight = criterion(row)
		for i in range(0, num_rows):
			if weight > top[i][1]:
				top.insert(i, (row, weight))
				del top[-1]
				break
	return top

def compute_results(num_rows, criteria):
	for i in range(0, num_rows):
		for criterion in criteria:
			if not criterion.__name__ in top:
				top[criterion.__name__] = []
			top[criterion.__name__].append(('dummy', -1))
	for row in matrix:
		results[row] = {}
		for criterion in criteria:
			weight = criterion(row)
			cname = criterion.__name__
			results[row][cname] = weight
			for i in range(0, num_rows):
				if weight > top[cname][i][1]:
					top[cname].insert(i, (row, weight))
					del top[cname][-1]
					break	 
		
def display_top(criterion, csv_name = ''):
	top_table = []
	for row in top[criterion]:
		if row[0] == 'dummy':
			continue
		top_table.append([row[1]] + matrix[row[0]])
	titles = [criterion, 'name', 'pod', 'in-pod'] + sorted(stats.iterkeys())
	print tabulate(top_table, headers=titles, tablefmt="orgtbl")
	
	if csv_name != '':
		write_csv(csv_name, titles, top_table, criterion)

def write_csv(csv_name, titles, top_table, criterion):
	with open(csv_name, 'w') as csvfile:
		print "Writing csv file:", csvfile.name
		writer = csv.writer(csvfile)
		writer.writerow(titles)
		writer.writerows(top_table)

def process_pod(path, pod_name):

	print "Handling files for pod:", pod_name
	files = os.listdir(path)
	files.sort()
	for f in files:
		if isfile(join(path, f)) and f.startswith(pod_name):
			print f
			read_table(path, f)
	transform_matrix()
	compute_results(len(matrix), [criterion_dispersion, criterion_max_peaks, criterion_num_changes])
	print 'Total rows', len(matrix)
	print 'Total filtered out:', len(filtered_out)
	print ''
	print 'Max peaks:'
	csv_name = join(path, 'csv-' + pod_name + '-max_peaks.csv')
	display_top('criterion_max_peaks', csv_name)
	print ''
	csv_name = join(path, 'csv-' + pod_name + '-dispersion.csv')
	print 'Dispersion:'
	#display_top('criterion_dispersion', csv_name)
	print ''
	csv_name = join(path, 'csv-' + pod_name + '-changes.csv')
	print 'Num changes:'
	#display_top('criterion_num_changes', csv_name)
		
def main():
	global stats, matrix, results, top
	parser = argparse.ArgumentParser()
	parser.add_argument('path', help='metrics dir')
	parser.add_argument('-p', '--pods', help='list of pods', nargs='+')
	args = parser.parse_args()

	for pod in args.pods:
		stats = {}
		matrix = {}
		results = {}
		top = {}
		process_pod(args.path, pod)

main()
