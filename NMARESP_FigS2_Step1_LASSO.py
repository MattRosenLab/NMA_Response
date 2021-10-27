"""
This script compute a LASSO reconstruction from noisy measurements. The noise 
added to the measurements are the random (non-zero mean) noise produced by the 
script 'Demo_test_automap_non_zero_mean.py'.

Change the variable `runner_id_automap` to produce test the knee image perturbations 
"""
import sys
import time
import tensorflow as tf
import numpy as np
import h5py
import scipy.io
from os.path import join 
import os.path

from optimization.gpu.operators import MRIOperator
from optimization.gpu.proximal import WeightedL1Prox, SQLassoProx2
from optimization.gpu.algorithms import SquareRootLASSO
from optimization.utils import estimate_sparsity, generate_weight_matrix
from tfwavelets.dwtcoeffs import get_wavelet
from tfwavelets.nodes import idwt2d
from PIL import Image
import matplotlib.image as mpimg;
import scipy.io

from adv_tools_PNAS.automap_config import src_data;
from adv_tools_PNAS.adversarial_tools import l2_norm_of_tensor
from adv_tools_PNAS.automap_tools import read_automap_k_space_mask
from utils import convert_automap_samples_to_tf_samples_in_image_domain


from adv_tools_PNAS.automap_tools import load_runner, read_automap_k_space_mask, compile_network, hand_f, sample_image;


src_noise = 'data_non_zero_mean';

runner_id_automap = 5 # Change to 12, to produce the knee image perturbations
N = 128
wavname = 'db2'
levels = 3
use_gpu = True
compute_node = 2
dtype = tf.float64;
sdtype = 'float64';
scdtype = 'complex128';
cdtype = tf.complex128
wav = get_wavelet(wavname, dtype=dtype);

if use_gpu:
    # os.environ["CUDA_VISIBLE_DEVICES"]= "%d" % (compute_node)
    # print('Compute node: {}'.format(compute_node))
    os.environ["CUDA_VISIBLE_DEVICES"]= "2,3"
else: 
    os.environ["CUDA_VISIBLE_DEVICES"]= "-1"
    
# if use_gpu:
#     os.environ["CUDA_VISIBLE_DEVICES"]= "%d" % (compute_node)
#     print('Compute node: {}'.format(compute_node))
# else: 
#     os.environ["CUDA_VISIBLE_DEVICES"]= "-1"

# Turn on soft memory allocation
tf_config = tf.compat.v1.ConfigProto()
tf_config.gpu_options.allow_growth = True
tf_config.log_device_placement = False
sess = tf.compat.v1.Session(config=tf_config)

dest_data = 'data_non_zero_mean';
dest_plots = 'plots_non_zero_mean';

if not (os.path.isdir(dest_data)):
    os.mkdir(dest_data);

if not (os.path.isdir(dest_plots)):
    os.mkdir(dest_plots);

# Parameters for the CS-algorithm
n_iter = 1000
tau = 0.6
sigma = 0.6
lam = 0.0001

############################################################################
###                     Build Tensorflow Graph                           ###
############################################################################

# Parameters for CS algorithm
pl_sigma = tf.compat.v1.placeholder(dtype, shape=(), name='sigma')
pl_tau   = tf.compat.v1.placeholder(dtype, shape=(), name='tau')
pl_lam   = tf.compat.v1.placeholder(dtype, shape=(), name='lambda')

# Build Primal-dual graph
tf_im = tf.compat.v1.placeholder(cdtype, shape=[N,N,1], name='image')
tf_samp_patt = tf.compat.v1.placeholder(tf.bool, shape=[N,N,1], name='sampling_pattern')

# For the weighted l^1-norm
pl_weights = tf.compat.v1.placeholder(dtype, shape=[N,N,1], name='weights')

tf_input = tf_im

op = MRIOperator(tf_samp_patt, wav, levels, dtype=dtype)
measurements = op.sample(tf_input)

tf_adjoint_coeffs = op(measurements, adjoint=True)
adj_real_idwt = idwt2d(tf.math.real(tf_adjoint_coeffs), wav, levels)
adj_imag_idwt = idwt2d(tf.math.imag(tf_adjoint_coeffs), wav, levels)
tf_adjoint = tf.complex(adj_real_idwt, adj_imag_idwt)

prox1 = WeightedL1Prox(pl_weights, pl_lam*pl_tau, dtype=dtype)
prox2 = SQLassoProx2(dtype=dtype)

alg = SquareRootLASSO(op, prox1, prox2, measurements, sigma=pl_sigma, tau=pl_tau, lam=pl_lam, dtype=dtype)

initial_x = op(measurements, adjoint=True)

result_coeffs = alg.run(initial_x)

real_idwt = idwt2d(tf.math.real(result_coeffs), wav, levels)
imag_idwt = idwt2d(tf.math.imag(result_coeffs), wav, levels)
tf_recovery = tf.complex(real_idwt, imag_idwt)

samp = np.swapaxes(np.fft.fftshift(np.array(h5py.File(join(src_data, 'k_mask.mat'), 'r')['k_mask']).astype(np.bool)), 0,1)
samp = np.expand_dims(samp, -1)

k_mask_idx1, k_mask_idx2 = read_automap_k_space_mask();

fname_data = f'automap_rID_{runner_id_automap}_random_pert.mat'
data_noise = scipy.io.loadmat(join(src_noise, fname_data))

HCP_nbr = 1002
data = scipy.io.loadmat(join(src_data, f'HCP_mgh_{HCP_nbr}_T2_subset_N_128.mat'))
mri_data = data['im']
im_nbrs = [37, 50, 76]
image = np.squeeze(data['im'][im_nbrs[-1], :, :])
image = image.astype(np.complex128);


sample = lambda im: sample_image(im, k_mask_idx1, k_mask_idx2)

fname_out_advonly_input = f'automap_input_advonly_orignet_rID_{runner_id_automap}_array.npy'
fname_out_advgauss_input = f'automap_input_adbgauss_orignet_rID_{runner_id_automap}_array.npy'


noisy_ksp_advonly_arr = np.load(join(dest_data, fname_out_advonly_input))
noisy_ksp_advgauss_arr = np.load(join(dest_data, fname_out_advgauss_input))

lasso_recon_advonly_arr = np.zeros(shape=(5,len(im_nbrs),128,128))
lasso_recon_advgauss_arr = np.zeros(shape=(5,len(im_nbrs),128,128))

with tf.compat.v1.Session() as sess:

    sess.run(tf.compat.v1.global_variables_initializer())
    weights = np.ones([128,128,1], dtype=sdtype);
    nbr_perts = len(data_noise.keys())-3

    for r_value in range(0,5):
        # print(r_value)
        # image = np.squeeze(mri_data[im_nbr, :, :])
        # image = image.astype(np.complex128);
        # image = np.expand_dims(image, -1)

        for i in range(len(im_nbrs)):

            print(r_value,i)

            # e_random = data_noise[f"e{i}"];

            
            # print(e_random.shape)
            # sys.exit()
            # noisy_ksp = e_random+sample(np.expand_dims(image[:,:,0],0))

            noisy_ksp_advonly = noisy_ksp_advonly_arr[r_value,i,:]
            noisy_ksp_advgauss = noisy_ksp_advgauss_arr[r_value,i,:]
            
            noisy_image_advonly = convert_automap_samples_to_tf_samples_in_image_domain(noisy_ksp_advonly, 
                                                                          k_mask_idx1,
                                                                          k_mask_idx2)

            noisy_image_advgauss = convert_automap_samples_to_tf_samples_in_image_domain(noisy_ksp_advgauss, 
                                                                          k_mask_idx1,
                                                                          k_mask_idx2)

            _rec_advonly = sess.run(tf_recovery, feed_dict={ 'tau:0': tau,
                                                 'lambda:0': lam,
                                                 'sigma:0': sigma,
                                                 'weights:0': weights,
                                                 'n_iter:0': n_iter,
                                                 'image:0': noisy_image_advonly,
                                                 'sampling_pattern:0': samp})

            _rec_advgauss = sess.run(tf_recovery, feed_dict={ 'tau:0': tau,
                                                 'lambda:0': lam,
                                                 'sigma:0': sigma,
                                                 'weights:0': weights,
                                                 'n_iter:0': n_iter,
                                                 'image:0': noisy_image_advgauss,
                                                 'sampling_pattern:0': samp})

            rec_advonly = np.abs(_rec_advonly[:,:,0]).astype(np.float64);
            rec_advgauss = np.abs(_rec_advgauss[:,:,0]).astype(np.float64);

            lasso_recon_advonly_arr[r_value,i,:,:] = rec_advonly
            lasso_recon_advgauss_arr[r_value,i,:,:] = rec_advgauss
            

        
    fname_out_advonly = f'lasso_output_advonly_rID_{runner_id_automap}_array.npy'
    fname_out_advgauss = f'lasso_output_adbgauss_rID_{runner_id_automap}_array.npy'


    np.save(join(dest_data, fname_out_advonly), lasso_recon_advonly_arr)
    np.save(join(dest_data, fname_out_advgauss), lasso_recon_advgauss_arr)

            # rec[rec > 1] = 1;
            # fname = f'lfi_im_rec_lasso_rID_{runner_id_automap}_HCP_{HCP_nbr}_im_nbr_{im_nbr}_pert_nbr_{i}.png';
            # pil_im = Image.fromarray(np.uint8(255*rec));
            # pil_im.save(join(dest_plots, fname))










