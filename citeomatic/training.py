import logging
import resource
from collections import defaultdict

import keras
import numpy as np
from citeomatic.neighbors import EmbeddingModel, make_ann
from keras.callbacks import ReduceLROnPlateau, TensorBoard


def rank_metrics(y, preds, max_num_true_multiplier=9):
    """
    Compute various ranking metrics for citation prediction problem.
    """
    y = np.array(y)
    preds = np.array(preds)
    argsort_y = np.argsort(y)[::-1]
    y = y[argsort_y]
    preds = preds[argsort_y]
    sorted_inds = np.argsort(preds)[::-1]  # high to lower
    num_true = int(np.sum(y))
    K = int(np.minimum(len(y) / num_true, max_num_true_multiplier))
    precision_at_num_positive = []
    recall_at_num_positive = []
    # precision at i*num_true
    for i in range(1, K + 1):
        correct = np.sum(y[sorted_inds[:num_true * i]])
        precision_at_num_positive.append(correct / float(num_true * i))
        recall_at_num_positive.append(correct / float(num_true))
    # mean rank of the true indices
    rank_of_true = np.argsort(sorted_inds)[y == 1]
    mean_rank = np.mean(rank_of_true) + 1
    return mean_rank, precision_at_num_positive, recall_at_num_positive


def test_model(
    model, corpus, test_generator, n=None, print_results=False, debug=False
):
    """
    Utility function to test citation prediction for one query document at a time.
    """
    metrics = defaultdict(list)  # this function supports multiple outputs
    if n is None:
        n = len(corpus.test_ids)
    for i in range(n):
        data, labels = next(test_generator)
        predictions = model.predict(data)
        if len(predictions) == len(labels):
            predictions = [predictions]
        for i in range(len(predictions)):
            preds = predictions[i].flatten()
            metrics_loop = rank_metrics(labels, preds)
            metrics[i].append(metrics_loop)
            if debug:
                print(metrics_loop)
                print()

    rank = {}
    precision = {}
    recall = {}
    for i in range(len(metrics)):
        r, pr, rec = zip(*metrics[i])
        min_len = np.min([len(i) for i in pr])
        rank[i] = r
        precision[i] = [i[:min_len] for i in pr]
        recall[i] = [i[:min_len] for i in rec]
        if print_results:
            print("Mean rank:", np.round(np.mean(rank[i], 0), 2))
            print(
                "Average Precisions at multiples of num_true:",
                np.round(np.mean(precision[i], 0), 2)
            )
            print(
                "Average Recalls at multiples of num_true:",
                np.round(np.mean(recall[i], 0), 2)
            )

    return rank, precision, recall


class ValidationCallback(keras.callbacks.Callback):
    def __init__(self, model, corpus, validation_generator):
        super().__init__()
        self.model = model
        self.corpus = corpus
        self.validation_generator = validation_generator
        self.losses = []

    def on_epoch_end(self, epoch, logs={}):
        self.losses.append(logs.get('loss'))
        test_model(
            self.model,
            self.corpus,
            test_generator=self.validation_generator,
            n=1000,
            print_results=True
        )
        logging.info()


class MemoryUsageCallback(keras.callbacks.Callback):
    def on_epoch_end(self, epoch, logs={}):
        logging.info(
            '\nCurrent memory usage: %s',
            resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1e6
        )


class UpdateANN(keras.callbacks.Callback):
    def __init__(self, corpus, featurizer, embedding_model, data_generator):
        self.corpus = corpus
        self.featurizer = featurizer
        self.embedding_model = embedding_model
        self.data_generator = data_generator

    def on_epoch_end(self, epoch, logs=None):
        logging.info(
            'Epoch %d ended. Retraining approximate nearest neighbors model.',
            epoch + 1
        )
        embedding_model_wrapped = EmbeddingModel(
            self.featurizer, self.embedding_model
        )
        ann = make_ann(
            embedding_model_wrapped,
            self.corpus,
            ann_trees=10,
            build_ann_index=True
        )
        self.data_generator.ann = ann


def train_text_model(
    corpus,
    model,
    embedding_model,
    featurizer,
    training_generator,
    validation_generator=None,
    data_generator=None,
    use_nn_negatives=False,
    samples_per_epoch=1000000,
    total_samples=5000000,
    debug=False,
    tensorboard_dir=None
):
    """
    Utility function for training citeomatic models.
    """

    samples_per_epoch = np.minimum(samples_per_epoch, total_samples)
    epochs = int(np.ceil(total_samples / samples_per_epoch))

    callbacks_list = []

    if debug:
        callbacks_list.append(MemoryUsageCallback())
    if tensorboard_dir is not None:
        callbacks_list.append(
            TensorBoard(
                log_dir=tensorboard_dir, histogram_freq=1, write_graph=True
            )
        )

    callbacks_list.append(
        ReduceLROnPlateau(
            verbose=1, patience=1, epsilon=0.01, min_lr=1e-4, factor=0.5
        )
    )
    if use_nn_negatives:
        callbacks_list.append(
            UpdateANN(corpus, featurizer, embedding_model, data_generator)
        )

    model.fit_generator(
        training_generator,
        samples_per_epoch=samples_per_epoch,
        callbacks=callbacks_list,
        nb_epoch=epochs,
        max_q_size=2,
        pickle_safe=False,
        validation_data=validation_generator,
        nb_val_samples=5000
    )

    return model