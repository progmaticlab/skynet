#!/usr/bin/python3

import datetime
import os
import threading
import time
import pandas as pd
import numpy as np
import copy
import matplotlib as mpl
mpl.use('Agg')
from matplotlib import pyplot as plt

from anomaly import AnomalyDetection

k1 = '#DC7633'
k2 = '#E74C3C'

#lock = threading.Lock()

processing = False
processed_column = "None"
anomaly_info = ""
anomalies_found = {}
normals_found = {}
df_matrix = None
progress = 'Waiting'
columns_handled = []
draw_all = False
column_filter = []


def update_matrix(matrix, columns = []):
    global df_matrix, column_filter
    new_matrix = copy.deepcopy(matrix)
    df_matrix = new_matrix
    column_filter = columns

def draw_anomaly(column, ranges, ts):
    fname = column + '.' + str(datetime.datetime.now()) + '.png'
    fig, ax = plt.subplots(1, 1, figsize=(6, 4))
    ax.plot(np.arange(ts.shape[0]), ts)
    for k in ranges.keys():
        if len(ranges[k]) > 0:
            for start, end in ranges[k]:
                c = k1 if k ==1 else k2
                ax.axvspan(start, end-1, color=c, alpha = 0.16 * k)
        plt.savefig(fname)
    plt.close(fig)


# s == reviews-v1|cluster.inbound|9080|http|reviews.default.svc.cluster.local.external.upstream_rq_time|P75
def get_metric(s):
    return s.split('|', 1)[1]

def get_pod(s):
    return s.split('|', 1)[0]

def get_service(s):
    return get_pod(s).split('-', 1)[0]


def process_anomalies(logging, column_filter=[]):
    global anomalies_found, normals_found, processed_column, anomaly_info, processing, df_matrix, progress, draw_all, columns_handled
    current_anomalies = copy.deepcopy(anomalies_found)
    current_normals = copy.deepcopy(normals_found)
    processed_column = "Starting"
    if not df_matrix or len(column_filter) == 0:
        return ''
    if not processing:
        current_anomalies = {}
        anomaly_info = ''
        processed_column = ''
        progress = 'Waiting'
        columns_handled = []
        return ''
    df = pd.DataFrame.from_dict(df_matrix)
    row_len = len(next(iter(df_matrix.values())))
    if row_len > 30:
        row_len = 30
    logging.info("ML samples: %s, columns: %s", str(row_len), str(column_filter))
    ad = AnomalyDetection(row_len)
    col_count = 0
    for column in df_matrix.keys():
        if not column in column_filter:
            current_anomalies.pop(column, None)
            current_normals.pop(column, None)
            continue
        try:
            col_count += 1
            logging.info("ML processing column %s", column)
            processed_column = column
            M = df[column].mean()
            ts = df[column].fillna(M).values
            samples, ranges, positions = ad.find_anomalies(ts)
            logging.info("Finished processing column %s", column)
            anomaly_info = "Anomaly in " + column + " ranges: " + str(ranges) + " positions: " + str(positions)
            logging.info("ML processing column %s", anomaly_info)
            anomaly = {
                'info': anomaly_info,
                'pod': get_pod(column),
                'service': get_service(column),
                'metric': get_metric(column),
                'ranges': ranges,
                'positions': positions,
                'ts': ts.tolist()
            }
            if (0 < len(positions) and (
                len(positions[1]) > 0 and positions[1][-1] > samples * 0.9 or
                len(positions[2]) > 0 and positions[2][-1] > samples * 0.9 or
                len(positions[3]) > 0 and positions[3][-1] > samples * 0.9)):
                # TODO: find another criteria for checking for already found anomalies, like timestamp
                if current_anomalies.get(column, {}).get('info') != anomaly_info:
                    current_anomalies[column] = anomaly
                    current_normals.pop(column, None)
                    if draw_all:
                        draw_anomaly(column, ranges, ts)
                    logging.info(anomaly_info)
            else:
                current_anomalies.pop(column, None)
                current_normals[column] = anomaly
                logging.info("Adding anomaly to normals: %s", column)
        except Exception as e:
            anomaly_info = "Shit happens with " + column + " " + str(e)
            logging.error("ERROR in processing column %s", column)
            logging.error(e, exc_info=True)
            ranges = []
            positions = []
        finally:
    #            with lock:
            columns_handled.append(column)
            progress = str(len(columns_handled)) + '/' + str(len(df_matrix))  
            
    processed_column = "None"
    # Wait for other threads to finish
#    while len(columns_handled) != len(df_matrix):
#        time.sleep(1)
    #with lock:
    columns_handled = []
    anomalies_found = copy.deepcopy(current_anomalies)
    logging.info("ANOMALIES FOUND: %s", str(anomalies_found))
    normals_found = copy.deepcopy(current_normals)
    return ''
