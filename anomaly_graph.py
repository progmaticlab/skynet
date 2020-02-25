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

lock = threading.Lock()

processing = False
processed_column = "None"
anomaly_info = ""
anomalies_found = {}
df_matrix = None
progress = 'Waiting'
columns_handled = []
draw_all = False

def update_matrix(matrix):
    global df_matrix
    new_matrix = copy.deepcopy(matrix)
    df_matrix = new_matrix

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

# column_filter is provided for multi-threading mode, though this mode for some reason is slower than single-thread and has some bugs for now
def process_anomalies(logging, column_filter=None):
    global anomalies_found, processed_column, anomaly_info, processing, df_matrix, progress, draw_all, columns_handled
    processed_column = "Starting"
    if not df_matrix:
        return ''
    if not processing:
        anomalies_found = {}
        anomaly_info = ''
        processed_column = ''
        progress = 'Waiting'
        columns_handled = []
        return ''
    df = pd.DataFrame.from_dict(df_matrix)
    ad = AnomalyDetection(30)
    col_count = 0
    for column in df_matrix.keys():
        if column_filter and not column.startswith(column_filter):
            continue
        try:
            col_count += 1
            logging.info("ML processing column %s", column)
            processed_column = column
            M = df[column].mean()
            ts = df[column].fillna(M).values
            samples, ranges, positions = ad.find_anomalies(ts)
            anomaly_info = "Anomaly in " + column + " ranges: " + str(ranges) + " positions: " + str(positions)
            if (len(positions[1]) > 0 and positions[1][-1] > samples * 0.9 or
               len(positions[2]) > 0 and positions[2][-1] > samples * 0.9):
                # TODO: find another criteria for checking for already found anomalies, like timestamp
                if anomalies_found.get(column) != anomaly_info:
                    anomalies_found[column] = anomaly_info
                    if not draw_all:
                        draw_anomaly(column, ranges, ts)
                    logging.info(anomaly_info)
            else:
                anomalies_found.pop(column, None)
            if draw_all:
                draw_anomaly(column, ranges, ts)
        except Exception as e:
            anomaly_info = "Shit happens with " + column + " " + str(e)
            logging.error("ERROR in processing column " + column + str(e))
            ranges = []
            positions = []
        finally:
            with lock:
                columns_handled.append(column)
            progress = str(len(columns_handled)) + '/' + str(len(df_matrix))  
            
    processed_column = "None"
    # Wait for other threads to finish
    while len(columns_handled) != len(df_matrix):
        time.sleep(1)
    with lock:
        columns_handled = []
    return ''
    