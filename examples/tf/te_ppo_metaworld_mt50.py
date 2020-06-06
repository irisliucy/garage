#!/usr/bin/env python3
"""This is an example to train Task Embedding PPO with PointEnv."""
# pylint: disable=no-value-for-parameter
import akro
import click
from metaworld.benchmarks import MT50
import numpy as np
import tensorflow as tf

from garage import InOutSpec, wrap_experiment
from garage.envs import GarageEnv, normalize
from garage.envs.multi_env_wrapper import MultiEnvWrapper
from garage.envs.multi_env_wrapper import round_robin_strategy
from garage.experiment.deterministic import set_seed
from garage.np.baselines import LinearMultiFeatureBaseline
from garage.sampler import LocalSampler
from garage.tf.algos import TEPPO
from garage.tf.algos.te import TaskEmbeddingWorker
from garage.tf.embeddings import GaussianMLPEncoder
from garage.tf.experiment import LocalTFRunner
from garage.tf.policies import GaussianMLPTaskEmbeddingPolicy


@click.command()
@click.option('--seed', default=1)
@click.option('--n_epochs', default=600)
@click.option('--batch_size_per_task', default=1024)
@wrap_experiment
def te_ppo_mt50(ctxt, seed, n_epochs, batch_size_per_task):
    """Train Task Embedding PPO with PointEnv.

    Args:
        ctxt (garage.experiment.ExperimentContext): The experiment
            configuration used by LocalRunner to create the snapshotter.
        seed (int): Used to seed the random number generator to produce
            determinism.
        n_epochs (int): Total number of epochs for training.
        batch_size_per_task (int): Batch size of samples for each task.

    """
    set_seed(seed)
    tasks = MT50.get_train_tasks().all_task_names
    envs = [normalize(GarageEnv(MT50.from_task(task))) for task in tasks]
    env = MultiEnvWrapper(envs,
                          sample_strategy=round_robin_strategy,
                          mode='del-onehot')

    latent_length = 4
    inference_window = 6
    batch_size = batch_size_per_task * len(tasks)
    policy_ent_coeff = 2e-2
    encoder_ent_coeff = 2.e-4
    inference_ce_coeff = 5e-2
    max_path_length = 100
    embedding_init_std = 0.01
    embedding_max_std = 0.02
    embedding_min_std = 1e-6
    policy_init_std = 1.0
    policy_max_std = None
    policy_min_std = None

    with LocalTFRunner(snapshot_config=ctxt) as runner:

        latent_lb = np.zeros(latent_length, )
        latent_ub = np.ones(latent_length, )
        latent_space = akro.Box(latent_lb, latent_ub)

        # trajectory space is (TRAJ_ENC_WINDOW, act_obs) where act_obs is a
        # stacked vector of flattened actions and observations
        obs_lb, obs_ub = env.observation_space.bounds
        obs_lb_flat = env.observation_space.flatten(obs_lb)
        obs_ub_flat = env.observation_space.flatten(obs_ub)
        traj_lb = np.stack([obs_lb_flat] * inference_window)
        traj_ub = np.stack([obs_ub_flat] * inference_window)
        traj_space = akro.Box(traj_lb, traj_ub)

        task_embed_spec = InOutSpec(env.task_space, latent_space)
        traj_embed_spec = InOutSpec(traj_space, latent_space)

        inference = GaussianMLPEncoder(
            name='inference',
            embedding_spec=traj_embed_spec,
            hidden_sizes=[20, 10],
            std_share_network=True,
            init_std=2.0,
            output_nonlinearity=tf.nn.tanh,
            min_std=embedding_min_std,
        )

        task_encoder = GaussianMLPEncoder(
            name='embedding',
            embedding_spec=task_embed_spec,
            hidden_sizes=[20, 20],
            std_share_network=True,
            init_std=embedding_init_std,
            max_std=embedding_max_std,
            output_nonlinearity=tf.nn.tanh,
            min_std=embedding_min_std,
        )

        policy = GaussianMLPTaskEmbeddingPolicy(
            name='policy',
            env_spec=env.spec,
            encoder=task_encoder,
            hidden_sizes=[32, 16],
            std_share_network=True,
            max_std=policy_max_std,
            init_std=policy_init_std,
            min_std=policy_min_std,
        )

        baseline = LinearMultiFeatureBaseline(
            env_spec=env.spec, features=['observations', 'tasks', 'latents'])

        algo = TEPPO(env_spec=env.spec,
                     policy=policy,
                     baseline=baseline,
                     inference=inference,
                     max_path_length=max_path_length,
                     discount=0.99,
                     lr_clip_range=0.2,
                     policy_ent_coeff=policy_ent_coeff,
                     encoder_ent_coeff=encoder_ent_coeff,
                     inference_ce_coeff=inference_ce_coeff,
                     entropy_method='max',
                     use_softplus_entropy=True,
                     optimizer_args=dict(
                         batch_size=32,
                         max_epochs=10,
                         learning_rate=1e-3,
                     ),
                     inference_optimizer_args=dict(
                         batch_size=32,
                         max_epochs=10,
                     ),
                     center_adv=True,
                     stop_entropy_gradient=True,
                     stop_ce_gradient=True)

        runner.setup(algo,
                     env,
                     sampler_cls=LocalSampler,
                     sampler_args=None,
                     worker_class=TaskEmbeddingWorker)
        runner.train(n_epochs=n_epochs, batch_size=batch_size, plot=False)


te_ppo_mt50()