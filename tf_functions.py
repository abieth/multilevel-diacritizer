import sys
from pathlib import Path
from collections import MutableSequence, Counter

import tensorflow as tf
from tensorflow.keras import Sequential
from tensorflow.keras.layers import Embedding, LSTM, Dense, TimeDistributed, Bidirectional
# tf.config.experimental_run_functions_eagerly(True)
import numpy as np

from processing import DIACRITICS, NUMBER, NUMBER_PATTERN, SEPARATED_SUFFIXES, SEPARATED_PREFIXES, MIN_STEM_LEN, \
    HAMZAT_PATTERN, ORDINARY_ARABIC_LETTERS_PATTERN, ARABIC_LETTERS

DATASET_FILE_NAME = 'Tashkeela-processed.zip'
WINDOW_SIZE = 21
SLIDING_STEP = WINDOW_SIZE // 4
OPTIMIZER = tf.keras.optimizers.RMSprop()
TF_CHAR_ENCODING = 'UTF8_CHAR'
MONITOR_VALUE = 'loss'


@tf.function
def tf_max_length_pairs(pairs: tf.Tensor):
    lengths = tf.strings.length(tf.strings.reduce_join(pairs, -1), TF_CHAR_ENCODING)
    return pairs[tf.argmax(lengths)]


@tf.function
def tf_separate_affixes(u_word: tf.string):

    def regex_match(x):
        return tf.strings.regex_full_match(x[0], x[1])

    prefixes = tf.constant(sorted(SEPARATED_PREFIXES.union([''])))
    suffixes = tf.constant(sorted(SEPARATED_SUFFIXES.union([''])))
    prefixes_patterns = tf.strings.join([tf.constant('^'), prefixes, tf.constant('.+')])
    suffixes_patterns = tf.strings.join([tf.constant('.+'), suffixes, tf.constant('$')])
    possible_prefixes = tf.map_fn(regex_match,
                                  tf.stack([tf.broadcast_to(u_word, prefixes_patterns.shape), prefixes_patterns], -1),
                                  tf.bool)
    possible_suffixes = tf.map_fn(regex_match,
                                  tf.stack([tf.broadcast_to(u_word, suffixes_patterns.shape), suffixes_patterns], -1),
                                  tf.bool)
    accepted_affixes = tf.TensorArray(tf.string, size=1, dynamic_size=True, clear_after_read=True, element_shape=(2,))
    accepted_affixes.write(accepted_affixes.size(), tf.constant(['', '']))  # Words with length less than the threshold.
    for i in tf.where(possible_prefixes)[:, 0]:
        for j in tf.where(possible_suffixes)[:, 0]:
            stem_len = tf.strings.length(u_word, TF_CHAR_ENCODING) - (tf.strings.length(prefixes[i], TF_CHAR_ENCODING) +
                                                                      tf.strings.length(suffixes[j], TF_CHAR_ENCODING))
            if stem_len >= MIN_STEM_LEN:
                accepted_affixes = accepted_affixes.write(accepted_affixes.size(),
                                                          tf.stack((prefixes[i], suffixes[j]), -1))
    accepted_affixes = accepted_affixes.stack()
    prefix_suffix = tf_max_length_pairs(accepted_affixes)
    p_len = tf.strings.length(prefix_suffix[0], TF_CHAR_ENCODING)
    s_len = tf.strings.length(prefix_suffix[1], TF_CHAR_ENCODING)
    w_len = tf.strings.length(u_word, TF_CHAR_ENCODING)
    return tf.stack([prefix_suffix[0], tf.strings.substr(u_word, p_len, w_len - (p_len + s_len), TF_CHAR_ENCODING),
                     prefix_suffix[1]])


@tf.function
def tf_word_to_pattern(word: tf.string):
    letters, diacritics = tf_separate_diacritics(word)
    u_word = tf.strings.reduce_join(letters)
    pre_st_suf = tf_separate_affixes(u_word)
    prefix, stem, suffix = pre_st_suf[0], pre_st_suf[1], pre_st_suf[2]
    stem = tf.strings.regex_replace(stem, 'ى', 'ا')
    stem = tf.strings.regex_replace(stem, HAMZAT_PATTERN.pattern, 'ء')
    stem = tf.strings.regex_replace(stem, ORDINARY_ARABIC_LETTERS_PATTERN.pattern, 'ح')
    return tf_merge_diacritics(tf.strings.unicode_split(tf.strings.join([prefix, stem, suffix]), 'UTF-8'), diacritics)


@tf.function
def tf_convert_to_pattern(d_text: tf.string):
    parts = tf.strings.split(tf.strings.regex_replace(d_text, r'(\w+|\p{P}|\s)', r'\1&'), '&')
    return tf.strings.reduce_join(tf.map_fn(tf_word_to_pattern, parts))


@tf.function
def tf_separate_diacritics(d_text: tf.string):
    letter_diacritic = tf.strings.split(
        tf.strings.regex_replace(d_text, r'(.[' + ''.join(DIACRITICS) + ']*)', r'\1&'), '&'
    )
    decoded = tf.strings.unicode_decode(letter_diacritic, 'UTF-8')
    letters, diacritics = decoded[:-1, :1], decoded[:-1, 1:]
    return [tf.strings.unicode_encode(x, 'UTF-8') for x in (letters, diacritics)]


@tf.function
def tf_merge_diacritics(letters: tf.string, diacritics: tf.string):
    merged_letters_diacritics = tf.strings.reduce_join((letters, diacritics), 0)
    return tf.strings.reduce_join(merged_letters_diacritics, 0)


@tf.function
def tf_normalize_entities(text: tf.string):
    return tf.strings.strip(tf.strings.regex_replace(tf.strings.regex_replace(tf.strings.regex_replace(
        tf.strings.regex_replace(text, NUMBER_PATTERN.pattern, NUMBER),
        r'([^\p{Arabic}\p{P}\d\s'+''.join(DIACRITICS)+'])+', ''),
        r'\p{P}+', ''), r'\s{2,}', ' '))


CHARS = sorted(ARABIC_LETTERS.union({NUMBER, ' '}))
DIACS = sorted(DIACRITICS.difference({'ّ'}).union({''}).union('ّ'+x for x in DIACRITICS.difference({'ّ', 'ْ'})))
LETTERS_TABLE = tf.lookup.StaticHashTable(
        tf.lookup.KeyValueTensorInitializer(tf.constant(CHARS), tf.range(1, len(CHARS)+1)), 0
)
DIACRITICS_TABLE = tf.lookup.StaticHashTable(
        tf.lookup.KeyValueTensorInitializer(tf.constant(DIACS), tf.range(1, len(DIACS)+1)), 0
)


@tf.function
def tf_encode(letters: tf.string, diacritics: tf.string):
    return LETTERS_TABLE.lookup(letters), DIACRITICS_TABLE.lookup(diacritics)


@tf.function
def tf_data_processing(text: tf.string):
    text = tf_normalize_entities(text)
    letters, diacritics = tf_separate_diacritics(text)
    encoded_letters, encoded_diacritics = tf_encode(letters, diacritics)
    return tf.pad(encoded_letters, [[0, 1]]), tf.pad(encoded_diacritics, [[0, 1]])


@tf.function
def no_padding_accuracy(y_true, y_pred):
    return tf.reduce_mean(tf.cast(tf.equal(y_true, tf.cast(tf.argmax(y_pred, axis=-1), tf.float32))[tf.greater(y_true, 0)], tf.float32))


def download_data(data_dir, download_url):
    assert isinstance(data_dir, Path)
    assert isinstance(download_url, str)
    data_dir = data_dir.expanduser()
    dataset_file_path = data_dir.joinpath(DATASET_FILE_NAME)
    tf.keras.utils.get_file(str(dataset_file_path.absolute()), download_url, cache_dir=str(data_dir.absolute()),
                            cache_subdir=str(data_dir.absolute()), extract=True)


def get_model(params_dir):
    assert isinstance(params_dir, Path)
    model = Sequential([
        Embedding(len(CHARS)+1, 128, input_length=WINDOW_SIZE),
        Bidirectional(LSTM(128, return_sequences=True, dropout=0.1)),
        Bidirectional(LSTM(64, return_sequences=True, dropout=0.1)),
        TimeDistributed(Dense(len(DIACS)+1))
    ], name='E128-BLSTM128-BLSTM64-w{}s{}'.format(WINDOW_SIZE, SLIDING_STEP))
    model.compile(OPTIMIZER, tf.keras.losses.SparseCategoricalCrossentropy(True), [no_padding_accuracy])
    last_iteration = 0
    weight_files = sorted([x.name for x in params_dir.glob(model.name + '-*.h5')])
    if len(weight_files) > 0:
        last_weights_file = str(params_dir.joinpath(weight_files[-1]).absolute())
        last_iteration = int(last_weights_file.split('-')[-1].split('.')[0])
        model.load_weights(last_weights_file)
    return model, last_iteration


def get_processed_window_dataset(file_paths, batch_size):
    dataset = tf.data.TextLineDataset(file_paths).map(tf_data_processing, tf.data.experimental.AUTOTUNE)
    dataset = dataset.unbatch().window(WINDOW_SIZE, SLIDING_STEP, drop_remainder=True)\
        .flat_map(lambda x, y: tf.data.Dataset.zip((x, y))).batch(WINDOW_SIZE, drop_remainder=True).batch(batch_size)
    size = dataset.reduce(0, lambda old, new: old + 1).numpy()
    return dataset.prefetch(tf.data.experimental.AUTOTUNE), size


def train(data_dir, params_dir, epochs, batch_size, early_stop):
    assert isinstance(data_dir, Path)
    assert isinstance(params_dir, Path)
    assert isinstance(epochs, int)
    assert isinstance(batch_size, int)
    assert isinstance(early_stop, int)
    train_file_paths = [str(p.absolute()) for p in data_dir.glob('*train*.txt')]
    val_file_paths = [str(p.absolute()) for p in data_dir.glob('*val*.txt')]
    print('Generating the training set...')
    train_dataset, train_steps = get_processed_window_dataset(train_file_paths, batch_size)
    model, last_iteration = get_model(params_dir)
    print('Generating the validation set...')
    val_dataset, val_steps = get_processed_window_dataset(val_file_paths, batch_size)
    print('Calculating diacritics weights...')
    labels = tf.data.TextLineDataset(train_file_paths).map(tf_data_processing, tf.data.experimental.AUTOTUNE)\
        .map(lambda x, y: y)
    diac_weights = np.zeros(len(DIACS)+1)
    for ls in labels:
        uniques, counts = np.unique(ls, return_counts=True)
        diac_weights[uniques] += counts
    diac_weights = np.max(diac_weights)/diac_weights
    model.fit(train_dataset.repeat(), steps_per_epoch=train_steps, epochs=epochs, validation_data=val_dataset.repeat(),
              validation_steps=val_steps, initial_epoch=last_iteration,
              class_weight=dict(enumerate(diac_weights)),
              callbacks=[tf.keras.callbacks.EarlyStopping(patience=early_stop, verbose=1, restore_best_weights=True,
                                                          monitor=MONITOR_VALUE),
                         tf.keras.callbacks.ModelCheckpoint(
                             str(params_dir.joinpath(model.name+'-{epoch:03d}.h5').absolute()), save_best_only=True,
                             save_weights_only=True, monitor=MONITOR_VALUE
                         ),
                         tf.keras.callbacks.TerminateOnNaN(), tf.keras.callbacks.TensorBoard(str(data_dir.absolute()))]
              )


class MultiPredictionSequence(MutableSequence):

    def __init__(self, window_size, sliding_step):
        self.data = []
        self.window_size = window_size
        self.sliding_step = sliding_step
        self.offset = 0

    def __getitem__(self, i):
        if len(self.data[i]) == 1:
            return self.data[i][0]
        else:
            c = Counter(self.data[i])
            return sorted(c.keys(), key=lambda x: c[x], reverse=True)[0]

    def __setitem__(self, i, o):
        if i == self.__len__():
            self.data.append([])
        self.data[i].append(o)

    def __delitem__(self, i):
        del self.data[i]

    def __len__(self):
        return len(self.data)

    def __iter__(self):
        for i in range(self.__len__()):
            yield self.__getitem__(i)

    def __repr__(self):
        return '<{} size={} data={}>'.format(self.__class__.__name__, self.__len__(), [x for x in self.data])

    def __str__(self):
        return str(list(self.__iter__()))

    def extend(self, iterable):
        for i, e in enumerate(iterable, self.offset):
            self.__setitem__(i, e)
        self.offset += self.sliding_step

    def insert(self, i, o):
        self.data.insert(i, o)


def test(data_dir, params_dir, batch_size):
    assert isinstance(data_dir, Path)
    assert isinstance(params_dir, Path)
    assert isinstance(batch_size, int)
    test_file_paths = [str(p.absolute()) for p in data_dir.glob('*test*.txt')]
    test_dataset, test_steps = get_processed_window_dataset(test_file_paths, batch_size)
    test_steps = int(test_steps)
    model, last_iteration = get_model(params_dir)
    print('Testing...')

    def test_batch(batch):
        x_batch, y_batch = batch
        x_best_sequence = MultiPredictionSequence(WINDOW_SIZE, SLIDING_STEP)
        y_best_sequence = MultiPredictionSequence(WINDOW_SIZE, SLIDING_STEP)
        p_best_sequence = MultiPredictionSequence(WINDOW_SIZE, SLIDING_STEP)
        for x, y in zip(x_batch, y_batch):
            x = x.numpy()
            y = y.numpy()
            y_pred = np.argmax(model.predict(np.reshape(x, (1, -1)))[0], axis=-1)
            x_best_sequence.extend(x)
            y_best_sequence.extend(y)
            p_best_sequence.extend(y_pred)
        x_best_sequence = np.array(x_best_sequence)
        y_best_sequence = np.array(y_best_sequence)
        p_best_sequence = np.array(p_best_sequence)
        der1 = der(x_best_sequence, y_best_sequence, p_best_sequence)
        wer1 = wer(x_best_sequence, y_best_sequence, p_best_sequence)
        der2 = der(x_best_sequence, y_best_sequence, p_best_sequence, True)
        wer2 = wer(x_best_sequence, y_best_sequence, p_best_sequence, True)
        return der1, wer1, der2, wer2

    cumulative_der1 = 0
    cumulative_wer1 = 0
    cumulative_der2 = 0
    cumulative_wer2 = 0
    s = 0
    for der1, wer1, der2, wer2 in map(test_batch, test_dataset):
        s += 1
        cumulative_der1 += der1
        cumulative_wer1 += wer1
        cumulative_der2 += der2
        cumulative_wer2 += wer2
        print_progress_bar(s, test_steps)
    print('\nDER1 = {:.2%} | DER2 = {:.2%} | WER1 = {:.2%} | WER2 = {:.2%}'.format(
        cumulative_der1/s, cumulative_der2/s, cumulative_wer1/s, cumulative_wer2/s
    ))


def der(x, y_true, y_pred, exclude_syntactic=False):
    if not exclude_syntactic:
        arabic_letters_pos = x > 2
    else:
        arabic_letters_pos = np.logical_and(x > 2, np.concatenate((x[1:], [1])) > 1)
    return np.nan_to_num(
        1 - np.sum(y_pred[arabic_letters_pos] == y_true[arabic_letters_pos]) / np.sum(arabic_letters_pos)
    )


def wer(x, y_true, y_pred, exclude_syntactic=False):
    no_pad = x > 0
    x = x[no_pad]
    y_true = y_true[no_pad]
    y_pred = y_pred[no_pad]
    y_true_words = np.split(y_true, np.nonzero(x == 1)[0])
    y_pred_words = np.split(y_pred, np.nonzero(x == 1)[0])
    num_correct = 0
    if not exclude_syntactic:
        for t_w, p_w in zip(y_true_words, y_pred_words):
            num_correct += int(np.all(t_w == p_w))
    else:
        for t_w, p_w in zip(y_true_words, y_pred_words):
            num_correct += int(np.all(t_w[:-1] == p_w[:-1]))
    return np.nan_to_num(1 - num_correct / len(y_true_words))


def print_progress_bar(current, maximum):
    assert isinstance(current, int)
    assert isinstance(maximum, int)
    progress_text = '[{:50s}] {:d}/{:d} ({:0.2%})'.format('=' * int(current / maximum * 50), current, maximum,
                                                          current / maximum)
    sys.stdout.write('\r' + progress_text)
    sys.stdout.flush()
