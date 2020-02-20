#!/usr/bin/python3

import os
import pandas as pd
import numpy as np
import matplotlib as mpl
mpl.use('Agg')
from matplotlib import pyplot as plt

from anomaly import AnomalyDetection

k1 = '#DC7633'
k2 = '#E74C3C'

def draw_graphs(matrix, filename, columns):
    #print("Anomaly graph\n")
    df = pd.DataFrame.from_dict(matrix)
    #import pdb; pdb.set_trace()
    ad = AnomalyDetection(150)
    #print(columns)
    for column in columns:
        print(column)
        print(matrix[column])
        fname = '{}_{}.png'.format(filename, column)
        M = df[column].mean()
        ts = df[column].fillna(M).values
        ranges, positions = ad.find_anomalies(ts)
        #print("Anomalies:\n")
        #print(ranges)
        #print(positions)
        fig, ax = plt.subplots(1, 1, figsize=(6, 4))
        ax.plot(np.arange(ts.shape[0]), ts)
        for k in ranges.keys():
            if len(ranges[k]) > 0:
                for start, end in ranges[k]:
                    c = k1 if k ==1 else k2
                    ax.axvspan(start, end-1, color=c, alpha = 0.16 * k)

        plt.savefig(fname)
