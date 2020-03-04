import pandas as pd
import numpy as np
import logging

import tensorflow as tf
from tensorflow import keras
from tensorflow.keras.layers import Dense, LSTM, Bidirectional
from tensorflow.keras.models import Sequential
from tensorflow.keras.callbacks import EarlyStopping


class AnomalyDetection(object):
    def __init__(self, base_sample_size, msx=[1]): #, 0.75, 1.25]):
        self.kernel_n = base_sample_size
        self.msx = msx

    def find_anomalies(self, series, bins=[2, 3, 4], verbose=0):
        X = self.__Z_scale(series)

        if np.count_nonzero(X) == 0:
            return {}, {}, {}

        min_norm = 10 ** 6
        min_idx = 0
        X_pred_final = None
        for i in self.msx:
            current_sample_n = int(self.kernel_n * i)
            X_pred, offset = self.__reconstruct(X, current_sample_n, verbose)
            norma = np.linalg.norm(X[offset:] - X_pred)

            if norma < min_norm:
                min_norm = norma
                min_idx = i
                X_pred_final = X_pred

        if min_idx == 0:
            raise Exception('Norm is to high for reconstructed TS')


        samples_n = int(self.kernel_n * min_idx)
        features_n = int(X_pred_final.shape[0] / samples_n)
        offset = X.shape[0] - X_pred_final.shape[0]

        X_ch = X[offset:].reshape(samples_n, features_n)
        X_pred_ch = X_pred_final.reshape(samples_n, features_n)

        dist = np.linalg.norm(X_ch - X_pred_ch, axis=-1)
        mean = dist.mean()
        std = dist.std()

        ranges = {}
        positions = {}
        for i in range(len(bins)):
            ranges[i+1] = []
            positions[i+1] = []

        for i, d in enumerate(dist):
            bucket = self.__get_bucket(d, mean, std, bins)
            if bucket > 0:
                ranges[bucket].append((offset + i * features_n, offset + (i + 1) * features_n))
                positions[bucket].append(i + 1)

        return samples_n, ranges, positions

    def __get_bucket(self, v, M, std, bins):
        i = 0
        t_stat = abs(M -v) / std
        for i in range(len(bins)):
            if t_stat < bins[i]:
                return i
        return i + 1

    def __Z_scale(self, X):
        mean = X.mean()
        std = X.std()
        return (X - mean) / (std + 0.00001)

    def __reconstruct(self, X, samples_n, epochs_n=40, verbose=0):
        features_n = int(X.shape[0] / samples_n)
        offset = X.shape[0] % samples_n

        logging.debug('Smaples = {}, feaures = {}, offset = {}'.format(samples_n, features_n, offset))
        X = X[offset:]

        X = X.reshape(samples_n, features_n)
        keras.backend.clear_session()
        model = Sequential()
        model.add(Bidirectional(LSTM(units=32, dropout=0.2, recurrent_dropout=0.2), input_shape=(features_n, 1)))
        model.add(Dense(features_n, activation='linear'))
        model.compile(optimizer='adam', loss='mean_squared_error', metrics=['mean_absolute_error'])

        dts = np.expand_dims(X, axis=2)
        model.fit(x=dts, y=X, batch_size=16, epochs=epochs_n, validation_split=0.1, verbose=verbose,
                  callbacks=[EarlyStopping(patience=2)])

        pts = model.predict(dts)
        return pts.reshape(-1), offset


if __name__ == '__main__':
    df = pd.read_csv('../data/stocks.csv')
    ad = AnomalyDetection(45)
    print(ad.find_anomalies(df['pgz'].values))
