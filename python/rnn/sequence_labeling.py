from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from builtins import input
from builtins import range

import numpy as np
import tensorflow as tf
import os
from datetime import datetime

from tensorflow.python import debug as tf_debug

####
# Sequence Labeling with variable-length sequences
# For sequence labelling, we want a prediction for each timestamp.
####
NUM_HIDDEN = 5
NUM_CLASS = 3
FEATURE_SIZE_PER_TIMESTEP = 5

### Data pipeline
def input_pipeline(filename, batch_size, epochs=None):
    file_list = [os.path.join(os.getcwd(), 'sequence_labeling_data', filename)]
    file_queue = tf.train.string_input_producer(file_list, num_epochs=epochs)
    reader = tf.TFRecordReader()
    _, serialized_example = reader.read(file_queue)
    sequence_features = {
        "inputs": tf.FixedLenSequenceFeature([FEATURE_SIZE_PER_TIMESTEP], dtype=tf.float32),
        "labels": tf.FixedLenSequenceFeature([], dtype=tf.int64)
    }
    _, sequence = tf.parse_single_sequence_example(
        serialized=serialized_example,
        sequence_features=sequence_features)

    actual_length = tf.shape(sequence["inputs"])[0]
    batch_lengths, batch_sequences, batch_labels = tf.train.batch(
        [actual_length, sequence["inputs"], sequence["labels"]],
        batch_size=batch_size,
        dynamic_pad=True,
        allow_smaller_final_batch=True,
        name="input_batching")
    return batch_lengths, batch_sequences, batch_labels


### Build Model
def inference(inputs, actual_lengths):
    cell = tf.contrib.rnn.LSTMCell(NUM_HIDDEN)
    outputs, current_state = tf.nn.dynamic_rnn(cell, inputs, dtype=tf.float32, sequence_length=actual_lengths)
    max_length = tf.shape(outputs, name='outputs_shape')[1]
    # Output layer weights & biases
    weights = tf.Variable(tf.truncated_normal([NUM_HIDDEN, NUM_CLASS]), dtype=tf.float32, name='output_weights')
    biases = tf.Variable(tf.constant(0.1, shape=[NUM_CLASS]), dtype=tf.float32, name='output_biases')
    # Flatten to apply same weights to all time steps.
    outputs_flat = tf.reshape(outputs, [-1, NUM_HIDDEN], name='outputs_flat')
    logits_flat = tf.add(tf.matmul(outputs_flat, weights), biases)
    predictions_flat = tf.nn.softmax(logits_flat)
    logits = tf.reshape(logits_flat, [-1, max_length, NUM_CLASS], name='logits')
    predictions = tf.reshape(predictions_flat, [-1, max_length, NUM_CLASS], name='predictions')
    return logits, predictions

## Cost function
def loss(logits, labels, actual_lengths):
    logits_flat = tf.reshape(logits, [-1, NUM_CLASS], name='logits_flat')
    labels_flat = tf.reshape(labels, [-1], name='labels_flat')
    cross_entropy_flat = tf.nn.sparse_softmax_cross_entropy_with_logits(logits=logits_flat, 
                                                                        labels=labels_flat, 
                                                                        name='cross_entropy_flat')
    # Mask the losses via sequence length
    max_length = tf.shape(labels, name='labels_shape')[1]
    mask = tf.sequence_mask(actual_lengths, max_length, dtype=tf.float32)
    mask = tf.reshape(mask, [-1], name='seq_mask_flat')
    masked_losses_flat = tf.multiply(mask, cross_entropy_flat, name='masked_losses_flat')
    masked_losses = tf.reshape(masked_losses_flat, tf.shape(labels), name='masked_losses')
    # Calculate mean loss
    mean_loss_by_example = tf.div(tf.reduce_sum(masked_losses, axis=1), 
                                                tf.cast(actual_lengths, tf.float32), 
                                                name='mean_loss_by_example')
    mean_loss = tf.reduce_mean(mean_loss_by_example, name='mean_loss')
    return mean_loss

    
## Error tracking 
def error(predictions, labels, actual_lengths):
    predictions_flat = tf.reshape(predictions, [-1, NUM_CLASS], name='predictions_flat')
    labels_flat = tf.reshape(labels, [-1])
    errors = tf.not_equal(labels_flat, tf.argmax(predictions_flat, 1))
    errors = tf.cast(errors, tf.float32, name='errors')
    max_length = tf.shape(labels)[1]
    mask = tf.sequence_mask(actual_lengths, max_length, dtype=tf.float32)
    mask = tf.reshape(mask, [-1])
    masked_errors = mask * errors
    masked_errors = tf.reshape(masked_errors, tf.shape(labels), name='masked_errors')
    mean_error_by_example = tf.div(tf.reduce_sum(masked_errors, axis=1), 
                                   tf.cast(actual_lengths, tf.float32), 
                                   name='mean_error_by_example')
    mean_error = tf.reduce_mean(mean_error_by_example, name='mean_error')
    return mean_error


def filter(predictions, actual_lengths):
    # predictions: batch_size * max_time_steps * num_classes
    # actual_lengths: list of actual sequence length in a batch
    max_length = tf.shape(predictions)[1]
    mask = tf.sequence_mask(actual_lengths, max_length, dtype=tf.bool)
    predictions_cls = tf.argmax(predictions, 2, name='predictions_cls')
    invalid_cls = tf.zeros(shape=tf.shape(predictions_cls), dtype=tf.int64) - 1
    return tf.where(mask, predictions_cls, invalid_cls, name='filter_predictions_cls')


def training(loss, learning_rate):
    optimizer = tf.train.AdamOptimizer(learning_rate)
    train_op = optimizer.minimize(loss)    
    return train_op


### Training
NUM_EPOCHS = 100
BATCH_SIZE = 3
DISPLAY_STEP = 10
LEARNING_RATE = 1e-3
TRAINING_SET_SIZE = 7
 
def train(model_dir):
    filename = 'Sequence_labeling.tfr'
    with tf.Graph().as_default():
        tf.set_random_seed(10)
        np.random.seed(10)
        # Build Graph
        lengths, sequences, labels = input_pipeline(filename, BATCH_SIZE)
        logits, _ = inference(sequences, lengths)
        avg_loss = loss(logits, labels, lengths)
        train_op = training(avg_loss, LEARNING_RATE)

        saver = tf.train.Saver()
        # Create & Initialize Session
        sess = tf.Session()
        ckpt = tf.train.get_checkpoint_state(model_dir)
        if ckpt and ckpt.model_checkpoint_path:
            print("Resume training after %s" % ckpt.model_checkpoint_path)
            saver.restore(sess, ckpt.model_checkpoint_path)
        else:
            print("Grand New training")
            init_op = tf.group(tf.global_variables_initializer(),
                               tf.local_variables_initializer())        
            sess.run(init_op)
        
        # Start QueueRunner
        coord = tf.train.Coordinator()
        threads = tf.train.start_queue_runners(sess=sess, coord=coord)
        try:
            #sess = tf_debug.LocalCLIDebugWrapperSession(sess)
            #sess.add_tensor_filter("has_inf_or_nan", tf_debug.has_inf_or_nan)

            # Training cycles
            for epoch in range(1, NUM_EPOCHS+1):
                epoch_avg_loss = 0.0
                total_batch = int(TRAINING_SET_SIZE / BATCH_SIZE) + 1 if TRAINING_SET_SIZE % BATCH_SIZE != 0 else int(TRAINING_SET_SIZE / BATCH_SIZE)
                for step in range(1, total_batch +1):
                    if coord.should_stop():
                        break
                    _, train_loss = sess.run([train_op, avg_loss]) 
                    epoch_avg_loss += train_loss / total_batch
                    assert not np.isnan(train_loss), 'Model diverged with loss = NaN'

                    if step % DISPLAY_STEP == 0:
                        print('%s: epoch %d, step %d, train_loss = %.6f'
                            % (datetime.now(), epoch, step, train_loss))
                if epoch % DISPLAY_STEP == 0:        
                    print('%s: epoch %d avg_loss = %.6f'
                        % (datetime.now(), epoch, epoch_avg_loss))
            
            ckpt_path = os.path.join(model_dir, 'model.ckpt')
            model_path = saver.save(sess, ckpt_path)

        except tf.errors.OutOfRangeError as e:
            print(e.error_code, e.message)
            print('Done!')
        
        finally:
            coord.request_stop()
        
        coord.join(threads)
        sess.close()
    print('Finished Training!')
    return model_path


def eval(model_dir):
    filename = 'Sequence_labeling.tfr'
    with tf.Graph().as_default():
        # Build Graph
        lengths, sequences, labels = input_pipeline(filename, BATCH_SIZE, epochs=1)
        _, predictions = inference(sequences, lengths)
        avg_error = error(predictions, labels, lengths)

        saver = tf.train.Saver()
        sess = tf.Session()
        # Restore from ckpt
        ckpt = tf.train.get_checkpoint_state(model_dir)
        if ckpt and ckpt.model_checkpoint_path:
            print("Restore from ", ckpt.model_checkpoint_path)
            saver.restore(sess, ckpt.model_checkpoint_path)
        else:
            print('No checkpoint file found')
            return

        sess.run(tf.local_variables_initializer())                 
        
        # Start QueueRunner
        coord = tf.train.Coordinator()
        threads = tf.train.start_queue_runners(sess=sess, coord=coord)
        
        total_count = 0
        error_count = 0.0
        try:
            #sess = tf_debug.LocalCLIDebugWrapperSession(sess)
            #sess.add_tensor_filter("has_inf_or_nan", tf_debug.has_inf_or_nan)
            while not coord.should_stop():
                value, num = sess.run([avg_error, tf.reduce_sum(lengths)])
                error_count += value * num
                total_count += num
                #print(error_count, value, num)

        except tf.errors.OutOfRangeError as e:
            print(e.error_code, e.message)
            print('Done evaluation for %d examples!' % total_count)
        
        finally:
            coord.request_stop()
        
        coord.join(threads)
        pred_error = error_count / total_count
        print('Finished Eval! Predictions Error = %.5f' % pred_error)   
        sess.close()
 

def pred(model_dir):
    filename = 'Sequence_labeling.tfr'
    with tf.Graph().as_default():
        # Build Graph
        lengths, sequences, labels = input_pipeline(filename, BATCH_SIZE, epochs=1)
        _, predictions = inference(sequences, lengths)
        pred_cls = filter(predictions, lengths)

        saver = tf.train.Saver()
        sess = tf.Session()
        # Restore from ckpt
        ckpt = tf.train.get_checkpoint_state(model_dir)
        if ckpt and ckpt.model_checkpoint_path:
            print("Restore from ", ckpt.model_checkpoint_path)
            saver.restore(sess, ckpt.model_checkpoint_path)
        else:
            print('No checkpoint file found')
            return

        sess.run(tf.local_variables_initializer())                 
        
        # Start QueueRunner
        coord = tf.train.Coordinator()
        threads = tf.train.start_queue_runners(sess=sess, coord=coord)
        
        preds = []
        total_count = 0
        try:
            #sess = tf_debug.LocalCLIDebugWrapperSession(sess)
            #sess.add_tensor_filter("has_inf_or_nan", tf_debug.has_inf_or_nan)
            while not coord.should_stop():
                pred, num = sess.run([pred_cls, tf.reduce_sum(lengths)])
                total_count += num
                preds.extend(pred)

        except tf.errors.OutOfRangeError as e:
            print(e.error_code, e.message)
            print('Done predictions for %d examples!' % total_count)
        
        finally:
            coord.request_stop()
        
        coord.join(threads)
        print('Finished Predictions! predictions = ', preds)   
        sess.close()
 

if __name__ == '__main__':
    model_dir = os.path.join(os.getcwd(), 'lbl_models')
    tf.gfile.MakeDirs(model_dir)
    for i in range(12):
        train(model_dir)
        eval(model_dir)
    pred(model_dir)