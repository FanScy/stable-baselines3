import os
from abc import ABC, abstractmethod
from typing import Union, List, Dict, Any, Optional

import gym
import numpy as np

from torchy_baselines.common.base_class import BaseRLModel # pytype: disable=pyi-error
from torchy_baselines.common.vec_env import VecEnv, sync_envs_normalization
from torchy_baselines.common.evaluation import evaluate_policy
from torchy_baselines.common.logger import Logger


class BaseCallback(ABC):
    """
    Base class for callback.

    :param verbose: (int)
    """
    def __init__(self, verbose: int = 0):
        super(BaseCallback, self).__init__()
        self.model = None  # type: BaseRLModel
        self.training_env = None  # type: Union[gym.Env, VecEnv, None]
        self.n_calls = 0  # type: int
        self.num_timesteps = 0  # type: int
        self.verbose = verbose
        self.locals = None  # type: Dict[str, Any]
        self.globals = None  # type: Dict[str, Any]
        self.logger = None  # type: Logger
        # Sometimes, for event callback, it is useful
        # to have access to the parent object
        self.parent = None  # type: Optional[BaseCallback]

    def init_callback(self, model: BaseRLModel) -> None:
        """
        Initialize the callback by saving references to the
        RL model and the training environment for convenience.
        """
        self.model = model
        self.training_env = model.get_env()
        self.logger = Logger.CURRENT
        self._init_callback()

    def _init_callback(self) -> None:
        pass

    def on_training_start(self, locals_: Dict[str, Any], globals_: Dict[str, Any]) -> None:
        # Those are reference and will be updated automatically
        self.locals = locals_
        self.globals = globals_
        self._on_training_start()

    def _on_training_start(self) -> None:
        pass

    def on_rollout_start(self) -> None:
        self._on_rollout_start()

    def _on_rollout_start(self) -> None:
        pass

    @abstractmethod
    def _on_step(self) -> bool:
        """
        :return: (bool) If the callback returns False, training is aborted early.
        """
        return True

    def __call__(self) -> bool:
        """
        This method will be called by the model. This is the equivalent to the callback function.
        :return: (bool) If the callback returns False, training is aborted early.
        """
        self.n_calls += 1
        # timesteps start at zero
        self.num_timesteps = self.model.num_timesteps + 1

        return self._on_step()

    def on_training_end(self) -> None:
        self._on_training_end()

    def _on_training_end(self) -> None:
        pass

    def on_rollout_end(self) -> None:
        self._on_rollout_end()

    def _on_rollout_end(self) -> None:
        pass


class EventCallback(BaseCallback):
    """
    Base class for triggering callback on event.

    :param callback: (Optional[BaseCallback]) Callback that will be called
        when an event is triggered.
    :param verbose: (int)
    """
    def __init__(self, callback: Optional[BaseCallback] = None, verbose: int = 0):
        super(EventCallback, self).__init__(verbose=verbose)
        self.callback = callback
        # Give access to the parent
        if callback is not None:
            self.callback.parent = self

    def init_callback(self, model: BaseRLModel) -> None:
        super(EventCallback, self).init_callback(model)
        self.callback.init_callback(self.model)

    def _on_training_start(self) -> None:
        self.callback.on_training_start(self.locals, self.globals)

    def _on_event(self) -> bool:
        if self.callback is not None:
            return self.callback()
        return True


class CallbackList(BaseCallback):
    def __init__(self, callbacks: List[BaseCallback]):
        super(CallbackList, self).__init__()
        assert isinstance(callbacks, list)
        self.callbacks = callbacks

    def _init_callback(self) -> None:
        for callback in self.callbacks:
            callback.init_callback(self.model)

    def _on_training_start(self) -> None:
        for callback in self.callbacks:
            callback.on_training_start(self.locals, self.globals)

    def _on_step(self) -> bool:
        continue_training = True
        for callback in self.callbacks:
            # # Update variables
            # callback.num_timesteps = self.num_timesteps
            # callback.n_calls = self.n_calls
            # Return False (stop training) if at least one callback returns False
            continue_training = callback() and continue_training
        return continue_training

    def _on_training_end(self) -> None:
        for callback in self.callbacks:
            callback.on_training_end()


class CheckpointCallback(BaseCallback):
    """
    Callback for saving a model every `save_freq` steps

    :param save_freq: (int)
    :param save_path: (str) Path to the folder where the model will be saved.
    :param name_prefix: (str) Common prefix to the saved models
    """
    def __init__(self, save_freq: int, save_path: str, name_prefix='rl_model', verbose=0):
        super(CheckpointCallback, self).__init__(verbose)
        self.save_freq = save_freq
        self.save_path = save_path
        self.name_prefix = name_prefix

    def _init_callback(self) -> None:
        # Create folder if needed
        if self.save_path is not None:
            os.makedirs(self.save_path, exist_ok=True)

    def _on_step(self) -> bool:
        if self.n_calls % self.save_freq == 0:
            path = os.path.join(self.save_path, f'{self.name_prefix}_{self.num_timesteps}_steps')
            self.model.save(path)
            if self.verbose > 1:
                print(f"Saving model checkpoint to {path}")
        return True


class ConvertCallback(BaseCallback):
    """
    Convert functional callback (old-style) to object.

    :param on_step: (callable)
    :param verbose: (int)
    """
    def __init__(self, callback, verbose=0):
        super(ConvertCallback, self).__init__(verbose)
        self.callback = callback

    def _on_step(self) -> bool:
        if self.callback is not None:
            return self.callback(self.locals, self.globals)
        return True


class EvalCallback(EventCallback):
    """
    Callback for evaluating an agent.

    :param eval_env: (Union[gym.Env, VecEnv]) The environment used for initialization
    :param callback_on_new_best: (Optional[BaseCallback]) Callback to trigger
        when there is a new best model according to the `mean_reward`
    :param n_eval_episodes: (int) The number of episodes to test the agent
    :param eval_freq: (int) Evaluate the agent every eval_freq call of the callback.
    :param log_path: (str) Path to a log file (.npz) where the evaluations
        will be saved. It will be updated at each evaluation.
    :param best_model_save_path: (str) Path to a folder where the best model
        according to performance on the eval env will be saved.
    :param deterministic: (bool) Whether the evaluation should
        use a stochastic or deterministic actions.
    :param verbose: (int)
    """
    def __init__(self, eval_env: Union[gym.Env, VecEnv],
                 callback_on_new_best: Optional[BaseCallback] = None,
                 n_eval_episodes: int = 5,
                 eval_freq: int = 10000,
                 log_path: str = None,
                 best_model_save_path: str = None,
                 deterministic: bool = True,
                 verbose: int = 1):
        super(EvalCallback, self).__init__(callback_on_new_best, verbose=verbose)
        self.n_eval_episodes = n_eval_episodes
        self.eval_freq = eval_freq
        self.best_mean_reward = -np.inf
        self.deterministic = deterministic
        if isinstance(eval_env, VecEnv):
            assert eval_env.num_envs == 1, "You must pass only one environment for evaluation"

        self.eval_env = eval_env
        self.best_model_save_path = best_model_save_path
        self.log_path = log_path
        self.evaluations_results = []
        self.evaluations_timesteps = []

    def _init_callback(self):
        # Does not work when eval_env is a gym.Env and training_env is a VecEnv
        # assert type(self.training_env) is type(self.eval_env), ("training and eval env are not of the same type",
        #                                                         "{} != {}".format(self.training_env, self.eval_env))

        # Create folders if needed
        if self.best_model_save_path is not None:
            os.makedirs(self.best_model_save_path, exist_ok=True)
        if self.log_path is not None:
            os.makedirs(os.path.dirname(self.log_path), exist_ok=True)

    def _on_step(self) -> bool:

        if self.n_calls % self.eval_freq == 0:
            # Sync training and eval env if there is VecNormalize
            sync_envs_normalization(self.training_env, self.eval_env)

            episode_rewards, _ = evaluate_policy(self.model, self.eval_env, n_eval_episodes=self.n_eval_episodes,
                                                 deterministic=self.deterministic, return_episode_rewards=True)

            if self.log_path is not None:
                self.evaluations_timesteps.append(self.num_timesteps)
                self.evaluations_results.append(episode_rewards)
                np.savez(self.log_path, timesteps=self.evaluations_timesteps, results=self.evaluations_results)

            mean_reward, std_reward = np.mean(episode_rewards), np.std(episode_rewards)
            if self.verbose > 0:
                print(f"Eval num_timesteps={self.num_timesteps}, "
                      f"episode_reward={mean_reward:.2f} +/- {std_reward:.2f}")

            if mean_reward > self.best_mean_reward:
                if self.verbose > 0:
                    print("New best mean reward!")
                if self.best_model_save_path is not None:
                    self.model.save(os.path.join(self.best_model_save_path, 'best_model'))
                self.best_mean_reward = mean_reward
                # Trigger callback if needed
                if self.callback is not None:
                    return self._on_event()

        return True


class StopTrainingOnRewardThreshold(BaseCallback):
    """
    Stop the training once a threshold in episodic reward
    has been reached (i.e. when the model is good enough).

    It must be used with the `EvalCallback`.

    :param reward_threshold: (float)  Minimum expected reward per episode
        to stop training.
    :param verbose: (int)
    """
    def __init__(self, reward_threshold: float, verbose: int = 0):
        super(StopTrainingOnRewardThreshold, self).__init__(verbose=verbose)
        self.reward_threshold = reward_threshold

    def _on_step(self) -> bool:
        assert self.parent is not None, ("`StopTrainingOnMinimumReward` callback must be used "
                                         "with an `EvalCallback`")
        # Convert np.bool to bool, otherwise callback() is False won't work
        continue_training = bool(self.parent.best_mean_reward < self.reward_threshold)
        if self.verbose > 0 and not continue_training:
            print(f"Stopping training because the mean reward {self.parent.best_mean_reward:.2f} "
                  f" is above the threshold {self.reward_threshold}")
        return continue_training


class EveryNTimesteps(EventCallback):
    """
    Trigger a callback every `n_steps` timesteps

    :param n_steps: (int) Number of timesteps between two trigger.
    :param callback: (BaseCallback) Callback that will be called
        when the event is triggered.
    """
    def __init__(self, n_steps: int, callback: BaseCallback):
        super(EveryNTimesteps, self).__init__(callback)
        self.n_steps = n_steps
        self.last_time_trigger = 0

    def _on_step(self) -> bool:
        if (self.num_timesteps - self.last_time_trigger) >= self.n_steps:
            self.last_time_trigger = self.num_timesteps
            return self._on_event()
        return True
