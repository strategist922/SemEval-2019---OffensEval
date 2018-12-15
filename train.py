# Use hate eval data, multi linked model for the subtasks, attention, sklearn metrics to calc precision and recall

from enum import Enum
import os
import sys

import numpy as np
import tensorflow as tf
from sklearn.metrics import precision_recall_fscore_support, accuracy_score

from config import DefaultConfig
from model import Model, Phase
from common import *


def generate_instances(
		data,
		max_doc,
		max_label,
		max_word_timesteps,
		max_char_timesteps,
		batch_size=128):
	n_batches = data.get_size() // batch_size
	data = data.as_list()

	# We are discarding the last batch for now, for simplicity.
	labels = np.zeros(
		shape=(
			n_batches,
			batch_size,
			max_label),
		dtype=np.float32)
	# sentence/document vector
	docs = np.zeros(
		shape=(
			n_batches,
			batch_size),
		dtype=np.int32)
	# sentence representations with word IDs
	sents_word = np.zeros(
		shape=(
			n_batches,
			batch_size,
			max_word_timesteps),
		dtype=np.int32)
	# sentence lengths as words
	sents_word_lengths = np.zeros(
		shape=(
			n_batches,
			batch_size),
		dtype=np.int32)
	# sentence representation with IDs of chars
	sents_char = np.zeros(
		shape=(
			n_batches,
			batch_size,
			max_char_timesteps),
		dtype=np.int32)
	# sentence lengths as characters
	sents_char_lengths = np.zeros(
		shape=(
			n_batches,
			batch_size),
		dtype=np.int32)

	for batch in range(n_batches):
		for idx in range(batch_size):
			(doc, tokens, token_chars, label) = data[(batch * batch_size) + idx]
			token_chars_flat = [char for word_token in token_chars for char in word_token]

			# Label
			labels[batch, idx, label] = 1

			# Sentences
			docs[batch, idx] = doc

			# Sequences
			timesteps = min(max_word_timesteps, len(tokens))
			timesteps_char = min(max_char_timesteps, len(token_chars_flat))

			# Sequence length (time steps)
			sents_word_lengths[batch, idx] = timesteps
			sents_char_lengths[batch, idx] = timesteps_char

			# Sentences (tweets)
			sents_word[batch, idx, :timesteps] = tokens[:timesteps]
			sents_char[batch, idx, :timesteps_char] = token_chars_flat[:timesteps_char]


	return (sents_word, sents_word_lengths, sents_char, sents_char_lengths, labels, docs)


def train_model(preproc, config, train_batches, validation_batches):
	train_batches_words, train_batches_words_lengths, \
	train_batches_chars, train_batches_chars_lengths, \
	train_labels, train_docs = train_batches

	validation_batches_words, validation_batches_words_lengths, \
	validation_batches_chars, validation_batches_chars_lengths, \
	validation_labels, validation_docs = validation_batches

	with tf.Session() as sess:
		with tf.variable_scope("model", reuse=False):
			train_model = Model(
				preproc,
				config,
				train_batches_words,
				train_batches_words_lengths,
				train_batches_chars,
				train_batches_chars_lengths,
				train_labels,
				train_docs,
				phase=Phase.Train)

		with tf.variable_scope("model", reuse=True):
			validation_model = Model(
				preproc,
				config,
				validation_batches_words,
				validation_batches_words_lengths,
				validation_batches_chars,
				validation_batches_chars_lengths,
				validation_labels,
				validation_docs,
				phase=Phase.Validation)

		sess.run(tf.global_variables_initializer())

		prev_validation_loss = 0.0

		config.print()
		print("================================================================================================================================================")
		print("Epoch\tTrain Loss\tVal Loss\tDelta\t\tAccuracy\\SKL\t\tPrecision\\SKL\t\tRecall\\SKL\tF1\\SKL")
		print("================================================================================================================================================")

		embedding_matrix = np.asarray(preproc.get_embeddings().get_data())
		for epoch in range(config.n_epochs):
			train_loss = 0.0
			validation_loss = 0.0
			accuracy = 0.0
			TP = 0.0
			TN = 0.0
			FP = 0.0
			FN = 0.0
			gold_labels = tf.zeros([0])
			pred_labels = tf.zeros([0])

			# train on all batches.
			for batch in range(train_batches_words.shape[0]):
				loss, _ = sess.run([train_model.loss, train_model.train_op], {
										train_model.embeddings: embedding_matrix,
										train_model.x: train_batches_words[batch],
										train_model.lens: train_batches_words_lengths[batch],
									    train_model.char_rep_lens: train_batches_chars_lengths[batch],
									    train_model.char_rep: train_batches_chars[batch],
									    train_model.y: train_labels[batch],
									    train_model.docs: train_docs[batch]
									})
				train_loss += loss

			# validation on all batches.
			for batch in range(validation_batches_words.shape[0]):
				loss, acc, batch_gold_lbl, batch_pred_lbl, batch_tp, batch_tn, batch_fp, batch_fn = sess.run([
								validation_model.loss, validation_model.accuracy,
								validation_model.gold_labels, validation_model.pred_labels,
								validation_model.TP, validation_model.TN,
								validation_model.FP, validation_model.FN], {
									validation_model.embeddings: embedding_matrix,
									validation_model.x: validation_batches_words[batch],
									validation_model.lens: validation_batches_words_lengths[batch],
									validation_model.char_rep_lens: validation_batches_chars_lengths[batch],
									validation_model.char_rep: validation_batches_chars[batch],
									validation_model.y: validation_labels[batch],
									validation_model.docs: validation_docs[batch]
								})

				gold_labels = tf.concat([gold_labels, batch_gold_lbl], axis=0)
				pred_labels = tf.concat([pred_labels, batch_pred_lbl], axis=0)

				validation_loss += loss
				accuracy += acc
				TP += batch_tp
				TN += batch_tn
				FP += batch_fp
				FN += batch_fn

			train_loss /= train_batches_words.shape[0]
			validation_loss /= validation_batches_words.shape[0]
			accuracy /= validation_batches_words.shape[0] * 0.01
			precision = TP / (TP + FP)
			recall = TP / (TP + FN)
			f1 = 2 * precision * recall / (precision + recall)

			skl_precision, skl_recall, skl_f1, _ = precision_recall_fscore_support(gold_labels.eval(),
																	  pred_labels.eval(), labels=[0,1,2,3,4,5,6], average='micro')
			skl_accuracy = accuracy_score(gold_labels.eval(), pred_labels.eval(), normalize=True) * 100

			print("%d\t%.2f\t\t%.2f\t\t%.4f\t\t%.2f\\%.2f\t\t%.4f\\%.4f\t\t%.4f\\%.4f\t%.4f\\%.4f" %
				(epoch, train_loss, validation_loss, prev_validation_loss - validation_loss, accuracy, skl_accuracy, precision, skl_precision, recall, skl_recall, f1, skl_f1))

			prev_validation_loss = validation_loss

# Usage: python train.py TASK WORD_EMBEDDINGS TRAIN_DATA TEST_DATA
#	where TASK = <A, B, C>

# Pre-trained word embeddings used:
#	English: https://nlp.stanford.edu/projects/glove/

DEFAULT_TRAINING_DATA_PARTITION = 80
DEFAULT_TASK_TYPE = TaskType.Subtask_A

def print_usage():
	print("Usage: python train.py WORD_EMBEDDINGS TRAIN_DATA TEST_DATA\n\twhere TASK = <A, B, C>\n\n")


if __name__ == "__main__":
	print("\n\n\n\n\n\n")

	if len(sys.argv) != 5:
		print_usage()

		path_embed = "C:\\Users\\shadeMe\\Documents\\ML\\Embeddings\\glove.twitter.27B.100d.txt"
	#	path_embed = "C:\\Users\\shadeMe\\Documents\\ML\\Embeddings\\wiki-news-300d-1M-subword.vec"

		(train, test) = DatasetFile("Data\\offenseval-training-v1.tsv")		\
						.merge(DatasetFile("Data\\offenseval-trial.txt"))	\
						.partition(DEFAULT_TRAINING_DATA_PARTITION)
	#					.merge(DatasetFile("Data\\HatEval\\offsense_eval_converted.txt", encoding='ansi').partition(50)[0])	\
		task_type = DEFAULT_TASK_TYPE
	else:
		task_type = sys.argv[1]
		if task_type == "A" or task_type == "a":
			task_type = TaskType.Subtask_A
		elif task_type == "B" or task_type == "b":
			task_type = TaskType.Subtask_B
		elif task_type == "C" or task_type == "c":
			task_type = TaskType.Subtask_C
		else:
			print_usage()
			sys.exit()

		path_embed = sys.argv[2]
		path_train = sys.argv[3]
		path_val = sys.argv[4]

		train = DatasetFile(path_train)
		test = DatasetFile(path_test)

	config = DefaultConfig()
	preproc = Preprocessor(task_type, config)

	preproc.load(path_embed, train, test)
	data_train = preproc.get_training_set()
	data_validation = preproc.get_validation_set()

	# Generate batches
	print("Generating batches...")

	train_batches = generate_instances(
		data_train,
		preproc.get_max_docs(),
		preproc.get_max_labels(),
		config.word_rnn_max_timesteps,
		config.char_rnn_max_timesteps,
		batch_size=config.batch_size)
	validation_batches = generate_instances(
		data_validation,
		preproc.get_max_docs(),
		preproc.get_max_labels(),
		config.word_rnn_max_timesteps,
		config.char_rnn_max_timesteps,
		batch_size=config.batch_size)

	# Train the model
	print("Begin training...\n\n\n\n")
	train_model(preproc, config, train_batches, validation_batches)