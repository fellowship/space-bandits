# -*- coding: utf-8 -*-
"""Wide_Deep_Bandits_BLR_TS.ipynb"""

## 02/27/2021 - Implemented initial_lr and lr_weight_decay
## 02/27/2021 - Implemented separate learning rate and learning rate decay for wide and deep networks

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import numpy as np
import torch
import torch.nn as nn
from scipy.stats import invgamma
from scipy.sparse import coo_matrix

class Wide_Model(nn.Module):
    def __init__(self, embed_size=100, n_action=2, embed_dim=64):
        ## Learns expected reward for each action given User ID
        ## Uses embeddings to 'memorize' individual users
        ## embed_size - size of the dictionary of embeddings
        ## embed_dim -  size of each embedding vector
        ## n_action - number of possible actions
        super(Wide_Model, self).__init__()
        self.embed_size = embed_size
        self.n_action = n_action
        self.embed_dim = embed_dim
        
        self.embedding = nn.Embedding(self.embed_size, self.embed_dim)
        self.lr = nn.Linear(self.embed_dim, self.n_action)
    
    def forward(self, user_idx, get_rep = False):
        ## Input: user ID
        ## get_rep - get the representations for training 
        x = self.embedding(user_idx)
        rep = x
        x = self.lr(x)

        if get_rep == True:
          return rep
        else:
          return x.squeeze(axis=0)
    
    def get_representation(self, user_id):
        """
        Given input user_id, returns the embedding as the 
        "z" vector. Used for Bayesian linear regression + TS
        """
        x = user_id
        with torch.no_grad():
            x = self.embedding(x)
        return x


class Deep_Model(nn.Module):
    def __init__(self, context_size=5, layer_sizes=[50], n_action=2):
        ## Learns expected reward for each action given context
        ## layer_sizes (list of integers): defines neural network architecture: n_layers = len(layer_sizes), 
        ## value is per-layer width. (default [50])
        super(Deep_Model, self).__init__()
        self.context_size = context_size
        self.layer_sizes = layer_sizes
        self.n_action = n_action

        self.layers = []
        self.build_model()
        self.activation = nn.ReLU()
        ##self.activation = nn.Sigmoid()
    
    def build_layer(self, inp_dim, out_dim):
        """Builds a layer in deep model """

        layer = nn.modules.linear.Linear(inp_dim,out_dim)
        nn.init.uniform_(layer.weight)
        name = f'layer {len(self.layers)}'
        self.add_module(name, layer)
        return layer
    
    def build_model(self):
        """
        Defines the actual NN model with fully connected layers.
        """
        for i, layer in enumerate(self.layer_sizes):
            if i==0:
                inp_dim = self.context_size
            else:
                inp_dim = self.layer_sizes[i-1]
            out_dim = self.layer_sizes[i]
            new_layer = self.build_layer(inp_dim, out_dim)
            self.layers.append(new_layer)

        output_layer = self.build_layer(out_dim, self.n_action)
        self.layers.append(output_layer)

    def forward(self, contexts, get_rep = False):
        """forward pass of the neural network"""
        ## Input: context
        ## get_rep - get the representations for training
        x = contexts
        for i, layer in enumerate(self.layers):
            x = layer(x)
            if i != len(self.layers)-1:
                x = self.activation(x)
                rep = x
        if get_rep == True:
          return rep
        else:
          return x.squeeze(axis=0)
    
    def get_representation(self, contexts):
        """
        Given input contexts, returns representation
        "z" vector.
        """
        x = contexts
        with torch.no_grad():
            for i, layer in enumerate(self.layers[:-1]):
                x = layer(x)
                x = self.activation(x)
        return x
  

class Wide_and_Deep_Model(nn.Module):
    def __init__(self, context_size=5, deep_layer_sizes=[50], n_action=2, embed_size=100, wide_embed_dim=64):
        super(Wide_and_Deep_Model, self).__init__()
        ## Combines the Wide model and Deep model
        self.n_action = n_action
        self.context_size = context_size
        self.deep_layer_sizes = deep_layer_sizes
        self.embed_size = embed_size
        self.wide_embed_dim = wide_embed_dim

        self.lr_cred = nn.Linear(self.n_action*2, self.n_action)
        self.lr_crep = nn.Linear(self.wide_embed_dim + self.deep_layer_sizes[-1], self.n_action)
        
        self.wide_model = Wide_Model(embed_size=self.embed_size, n_action=self.n_action, embed_dim=self.wide_embed_dim)
        self.deep_model = Deep_Model(context_size=self.context_size, layer_sizes=self.deep_layer_sizes, n_action=self.n_action)
    
    def forward(self, wide_input, deep_input, combine_method='add'):

        possible_combine_methods = ['add','concat_reward','concat_reward_llr','concat_representation_llr']
        if combine_method not in possible_combine_methods:
          raise NameError('combine_method must be "add","concat_reward","concat_reward_llr", or "concat_representation_llr"')

        if combine_method == 'add':
          ## Get reward ouputs from wide and deep model
          x_wide = self.wide_model(wide_input)
          x_deep = self.deep_model(deep_input)
          ## Add the outputs from wide and deep model
          x = x_wide + x_deep
        
        elif combine_method == 'concat_reward':
          ## Get reward ouputs from wide and deep model
          x_wide = self.wide_model(wide_input)
          x_deep = self.deep_model(deep_input)
          ## Concatenate outputs from wide and deep model
          if len(x_wide.size()) == 1:
            x = torch.cat((x_wide,x_deep))
          elif len(x_wide.size()) > 1:
            x = torch.cat((x_wide,x_deep), dim=1)
        
        elif combine_method == 'concat_reward_llr':
          ## Get reward ouputs from wide and deep model
          x_wide = self.wide_model(wide_input)
          x_deep = self.deep_model(deep_input)
          ## Concatenate outputs from wide and deep model
          if len(x_wide.size()) == 1:
            x = torch.cat((x_wide,x_deep))
          elif len(x_wide.size()) > 1:
            x = torch.cat((x_wide,x_deep), dim=1)
          ## One final linear layer to predict rewards
          x = self.lr_cred(x)

        elif combine_method == 'concat_representation_llr':
          ## Get last-layer representations from wide and deep model
          wide_rep = self.wide_model(wide_input, get_rep = True)
          deep_rep = self.deep_model(deep_input, get_rep = True)
          ## Concatenate representations from wide and deep model
          if len(wide_rep.size()) == 1:
            combine_rep = torch.cat([wide_rep, deep_rep])
          elif len(wide_rep.size()) > 1:
            combine_rep = torch.cat([wide_rep, deep_rep], dim=1)
          ## One final linear layer to predict rewards
          x = self.lr_crep(combine_rep)

        return x.squeeze(-1)
    
    def get_representation(self, wide_input, deep_input):
      wide_rep = self.wide_model.get_representation(wide_input)
      deep_rep = self.deep_model.get_representation(deep_input)

      if len(wide_rep.size()) == 1:
        combine_rep = torch.cat([wide_rep, deep_rep])
      elif len(wide_rep.size()) > 1:
        combine_rep = torch.cat([wide_rep, deep_rep], dim=1)
      
      return combine_rep

## 01/19/21 Copied from space-bandits, modeified to handle user IDs

"""Define a data buffer for contextual bandit algorithms."""

import warnings
warnings.filterwarnings("ignore", category=DeprecationWarning)

class ContextualDataset(object):
    """The buffer is able to append new data, and sample random minibatches."""

    def __init__(self, context_dim, num_actions, memory_size=-1, intercept=False):
        """Creates a ContextualDataset object.
        The data is stored in attributes: contexts and rewards.
        The sequence of taken actions are stored in attribute actions.
        Args:
          context_dim: Dimension of the contexts.
          num_actions: Number of arms for the multi-armed bandit.
          memory_size: Specify the number of examples to store in memory.
            if memory_size = -1, all data will be stored.
          intercept: If True, it adds a constant (1.0) dimension to each context X,
            at the end.
        """

        self._context_dim = context_dim
        self._num_actions = num_actions
        self.contexts = None
        self.scaled_contexts = None
        self.rewards = None
        self.user_ids = []
        self.actions = []
        self.memory_size = memory_size
        self.intercept = intercept
        self.scaling_data = []

    def add(self, user_id, context, action, reward):
        """Adds a new triplet (context, action, reward) to the dataset.
        The reward for the actions that weren't played is assumed to be zero.
        Args:
          user_id: User ID, usually an integer
          context: A d-dimensional vector with the context.
          action: Integer between 0 and k-1 representing the chosen arm.
          reward: Real number representing the reward for the (context, action).
        """
        if not isinstance(context, torch.Tensor):
            context = torch.tensor(context.astype(float))
        if len(context.shape) > 1:
            context = context.reshape(-1)
        if self.intercept:
            c = context[:]
            c = torch.cat((c, torch.tensor([1.0]).double()))
            c = c.reshape((1, self.context_dim + 1))
        else:
            if type(context) == type(torch.tensor(0)):
                c = context[:].reshape((1, self.context_dim))
            else:
                c = torch.tensor(context[:]).reshape((1, self.context_dim))



        if self.contexts is None:

            self.contexts = c
        else:
            self.contexts = torch.cat((self.contexts, c))



        r = torch.zeros((1, self.num_actions))
        r[0, action] = float(reward)
        if self.rewards is None:
            self.rewards = r
        else:
            self.rewards = torch.cat((self.rewards, r))


        self.actions.append(action)
        self.user_ids.append(user_id)

        #Drop oldest example if memory constraint
        if self.memory_size != -1:
            if self.contexts.shape[0] > self.memory_size:
                self.contexts = self.contexts[1:, :]
                self.rewards = self.rewards[1:, :]
                self.actions = self.actions[1:]
                self.user_ids = self.user_ids[1:]
            #Assert lengths match
            assert len(self.actions) == len(self.rewards)
            assert len(self.actions) == len(self.contexts)
            assert len(self.actions) == len(self.user_ids)

    def _replace_data(self, user_ids=None, contexts=None, actions=None, rewards=None):
        if contexts is not None:
            self.contexts = contexts
        if actions is not None:
            self.actions = actions
        if rewards is not None:
            self.rewards = rewards
        if user_ids is not None:
            self.user_ids = user_ids

    def _ingest_data(self, user_ids, contexts, actions, rewards):
        """Ingests bulk data."""
        if isinstance(rewards, pd.DataFrame) or isinstance(rewards, pd.Series):
            rewards = rewards.values
        if isinstance(actions, pd.DataFrame) or isinstance(actions, pd.Series):
            actions = actions.values
        if isinstance(user_ids, pd.DataFrame) or isinstance(user_ids, pd.Series):
            user_ids = user_ids.values
        if isinstance(contexts, pd.DataFrame) or isinstance(contexts, pd.Series):
            contexts = contexts.values

        if not isinstance(rewards, torch.Tensor):
            rewards = torch.tensor(rewards)
        if not isinstance(actions, torch.Tensor):
            actions = torch.tensor(actions)
        if not isinstance(user_ids, torch.Tensor):
            user_ids = torch.tensor(user_ids)
        if not isinstance(contexts, torch.Tensor):
            contexts = torch.tensor(contexts)

        data_length = len(rewards)

        if self.memory_size != -1:
            if data_length + len(self.rewards) > self.memory_size:
                print('Cannot add more examples: ')
                raise Exception('Too many examples for specified memory_size.')

        try:
            contexts = contexts.reshape(-1, self.context_dim)
        except:
            print('Got bad data contexts shape: ', contexts.shape)
            raise Exception('Expected: ({}, {})'.format(data_length, self.context_dim))

        if self.intercept:
            #add intercepts
            contexts = torch.cat([contexts, torch.ones((data_length, 1)).double()], dim=1)

        try:
            assert len(contexts) == data_length
            assert len(actions) == data_length
        except AssertionError:
            raise AssertionError('Data lengths inconsistent.')

        if self.contexts is None:
            self.contexts = contexts
        else:
            self.contexts = torch.cat((self.contexts, contexts))

        rewards_array = coo_matrix((np.array(rewards), (np.arange(data_length), np.array(actions)))).toarray()
        rewards_array = torch.tensor(rewards_array).float()
        if self.rewards is None:
            self.rewards = rewards_array
        else:
            self.rewards = torch.cat((self.rewards, rewards_array.float()))

        self.actions = self.actions + list(actions)
        self.user_ids = self.user_ids + list(user_ids)

    def get_batch(self, batch_size=512):
        """Returns a random minibatch of (contexts, rewards) with batch_size."""
        n, _ = self.contexts.shape
        ind = np.random.choice(range(n), batch_size)
        return torch.tensor(self.user_ids)[ind], self.contexts[ind, :], self.rewards[ind, :], torch.tensor(self.actions)[ind]

    def get_data(self, action):
        """Returns all (user_id, context, reward) where the action was played."""
        n, _ = self.contexts.shape
        ind = np.argwhere(np.array(self.actions) == action).reshape(-1)
        return torch.tensor(self.user_ids)[ind], self.contexts[ind, :], self.rewards[ind, action]
    
    def get_user_freq(self, user_id):
        """Returns the number of times an user_id occurs in data"""
        user_id = torch.tensor(user_id).tolist()
        user_arr = np.array(self.user_ids)
        user_freq = len(np.where(user_arr == user_id)[0])
        return user_freq

    def get_data_with_weights(self):
        """Returns all observations with one-hot weights for actions."""
        weights = torch.zeros((self.contexts.shape[0], self.num_actions))
        a_ind = np.array([(i, val) for i, val in enumerate(self.actions)])
        weights[a_ind[:, 0], a_ind[:, 1]] = 1.0
        return torch.tensor(self.user_ids)[ind], self.contexts, self.rewards, weights

    def get_batch_with_weights(self, batch_size, scaled=False):
        """Returns a random mini-batch with one-hot weights for actions."""

        n, _ = self.contexts.shape
        if self.memory_size == -1:
            # use all the data
            ind = np.random.choice(range(n), batch_size)
        else:
            # use only buffer (last buffer_s obs)
            ind = np.random.choice(range(max(0, n - self.memory_size), n), batch_size)

        weights = torch.zeros((batch_size, self.num_actions))
        sampled_actions = torch.tensor(self.actions)[ind]
        a_ind = torch.tensor([(i, val) for i, val in enumerate(sampled_actions)])
        weights[a_ind[:, 0], a_ind[:, 1]] = 1.0
        if scaled:
            ctx = self.scaled_contexts[ind, :]
        else:
            ctx = self.contexts[ind, :]
        return torch.tensor(self.user_ids)[ind], ctx, self.rewards[ind, :], weights


    def get_batch_with_weights_recent(self, batch_size, n_recent=0, scaled=False):
        """Returns a random mini-batch with one-hot weights for actions."""
        
        n, _ = self.contexts.shape
        if self.memory_size == -1:
            # use all the data
            ind = np.random.choice(range(n), batch_size)
        else:
            # use only buffer (last buffer_s obs)
            ind = np.random.choice(range(max(0, n - self.memory_size), n), batch_size)
        
        if n_recent > 0:
            ## returns the n_recent most recently added data points
            if n_recent > batch_size:
                n_recent = batch_size
            if n_recent > n:
                n_recent = n
            ind[:n_recent] = range(n-n_recent,n)
        
        weights = torch.zeros((batch_size, self.num_actions))
        sampled_actions = torch.tensor(self.actions)[ind]
        a_ind = torch.tensor([(i, val) for i, val in enumerate(sampled_actions)])
        weights[a_ind[:, 0], a_ind[:, 1]] = 1.0
        if scaled:
            ctx = self.scaled_contexts[ind, :]
        else:
            ctx = self.contexts[ind, :]
        return torch.tensor(self.user_ids)[ind], ctx, self.rewards[ind, :], weights

    def num_points(self, f=None):
        """Returns number of points in the buffer (after applying function f)."""
        if f is not None:
            return f(self.contexts.shape[0])
        return self.contexts.shape[0]

    def scale_contexts(self, contexts=None):
        """
        Performs mean/std scaling operation on contexts.
        if contexts is provided as argument, returns scaled version
            (scaled by statistics of data in dataset.)
        """
        means = self.contexts.mean(dim=0)
        stds = self.contexts.std(dim=0)
        stds[stds==0] = 1
        self.scaled_contexts = self.contexts.clone()
        for col in range(self._context_dim):
            self.scaled_contexts[:, col] -= means[col]
            self.scaled_contexts[:, col] /= stds[col]
        if contexts is not None:
            if not isinstance(contexts, torch.Tensor):
                contexts = torch.tensor(contexts)
            result = contexts
            for col in range(self._context_dim):
                result[:, col] -= means[col]
                result[:, col] /= stds[col]
            return result

    def get_contexts(self, scaled=False):
        if scaled:
            return self.scaled_contexts
        else:
            return self.contexts

    def get_user_ids(self):
        return torch.tensor(self.user_ids)

    def __len__(self):
        return len(self.actions)

    @property
    def context_dim(self):
        return self._context_dim

    @property
    def num_actions(self):
        return self._num_actions

    @property
    def contexts(self):
        return self._contexts

    @contexts.setter
    def contexts(self, value):
        self._contexts = value

    @property
    def actions(self):
        return self._actions
    
    @actions.setter
    def actions(self, value):
        self._actions = value

    @property
    def rewards(self):
        return self._rewards

    @rewards.setter
    def rewards(self, value):
        self._rewards = value

    @property
    def user_ids(self):
        return self._user_ids
    
    @user_ids.setter
    def user_ids(self, value):
        self._user_ids = value

class Wide_Deep_Bandits():
    def __init__(
        self,
        num_actions,
        num_features,
        wide_embed_size=100, ## Size of embedding dictionary for the wide model (int)
        wide_embed_dim=64, ## Dimension of embedding for the wide model (int)
        deep_layer_sizes=[50], ## deep_layer_sizes (list of integers): defines deep neural network architecture: n_layers = len(layer_sizes)
        wd_combine_method = 'concat_representation_llr', ## Method for combining the wide and deep models in the wide+deep model, 'concat_representation_llr' (default) seems to work the best for Bayesian linear regression
        update_freq_nn = 1, ## Frequency to update the model, default updates model for every data point (int)
        update_freq_lr = 1, ## Frequency for updates to bayesian linear regressor (int)
        num_epochs = 1, ## Number of steps to Train for each update (int)
        model_type = 'wide_deep', ## model_type = 'wide', 'deep', or 'wide_deep'
        a0=6, ## initial alpha value (int)
        b0=6, ## initial beta_0 value (int)
        lambda_prior=0.25, ## lambda prior parameter(float)
        initial_pulls=100, ## number of random pulls before greedy behavior (int)
        initial_lr_wide=0.01,## initial learning rate for wide network training (float, default same as torch.optim.RMSProp default)
        initial_lr_deep=0.01,## initial learning rate for deep network training (float, default same as torch.optim.RMSProp default)
        lr_decay_rate_wide=0.0,## learning rate decay for wide network updates (float)
        lr_decay_rate_deep=0.0,## learning rate decay for deep network updates (float)
        reset_lr=True, ## whether to reset learning rate when retraining network (bool)
        batch_size = 512, ## size of mini-batch to train at each step (int)
        max_grad_norm=5.0, ## maximum gradient value for gradient clipping (float)
        do_scaling=True, ## whether to automatically scale features (bool)
        name='wide_deep_bandits'):

        ## Raise error if model_type is not one of the available models
        possible_models = ['deep','wide','wide_deep']
        if model_type not in possible_models:
          raise NameError('model_type must be "deep", "wide", or "wide_deep"')
        
        ## Raise error if wd_combine_method is not one of the available methods
        possible_combine_methods = ['add','concat_reward','concat_reward_llr','concat_representation_llr']
        if wd_combine_method not in possible_combine_methods:
          raise NameError('wd_combine_method must be "add","concat_reward","concat_reward_llr", or "concat_representation_llr"')

        self.name = name
        self.model_type = model_type
        self.wide_embed_dim = wide_embed_dim
        self.wide_embed_size = wide_embed_size
        self.deep_layer_sizes = deep_layer_sizes
        self.max_grad_norm = max_grad_norm
        self.wd_combine_method = wd_combine_method
        self.do_scaling = do_scaling
        self.num_actions = num_actions
        self.context_dim = num_features
        self.initial_lr_wide = initial_lr_wide
        self.initial_lr_deep = initial_lr_deep
        self.lr_wide = initial_lr_wide
        self.lr_deep = initial_lr_deep
        self.lr_decay_rate_wide = lr_decay_rate_wide
        self.lr_decay_rate_deep = lr_decay_rate_deep
        self.reset_lr = reset_lr
        self.batch_size = batch_size
        

        ## Initialize model and optimizer depeding on model_type
        if self.model_type == 'deep':
          self.deep_model = Deep_Model(context_size=self.context_dim,
                                       layer_sizes=self.deep_layer_sizes,
                                       n_action=self.num_actions)
          #self.optim = torch.optim.RMSprop(self.deep_model.parameters(), lr=self.initial_lr)
          param_dict = [{'params': self.deep_model.parameters(), 'lr': self.initial_lr_deep}]
          self.param_dict = param_dict
          self.initial_param_dict = param_dict
          self.optim = self.select_optimizer()
          self.latent_dim = self.deep_layer_sizes[-1]

        if self.model_type == 'wide':
          self.wide_model = Wide_Model(embed_size=self.wide_embed_size, 
                                      n_action=self.num_actions, 
                                      embed_dim=self.wide_embed_dim)
          #self.optim = torch.optim.RMSprop(self.wide_model.parameters(), lr=self.initial_lr)
          param_dict = [{'params': self.wide_model.parameters(), 'lr': self.initial_lr_wide}]
          self.param_dict = param_dict
          self.initial_param_dict = param_dict
          self.optim = self.select_optimizer()
          self.latent_dim = self.wide_embed_dim
        

        if self.model_type == 'wide_deep':
          self.wide_deep_model = Wide_and_Deep_Model(context_size=self.context_dim,
                                                    deep_layer_sizes=self.deep_layer_sizes,
                                                    embed_size=self.wide_embed_size, 
                                                    n_action=self.num_actions, 
                                                    wide_embed_dim=self.wide_embed_dim) 
          #self.optim = torch.optim.RMSprop(self.wide_deep_model.parameters(), lr=self.initial_lr)
          param_dict = [{'params': self.wide_deep_model.wide_model.parameters(), 'lr': self.initial_lr_wide},
                        {'params': self.wide_deep_model.deep_model.parameters(), 'lr': self.initial_lr_deep},
                        {'params': self.wide_deep_model.lr_cred.parameters(), 'lr': 0.01},
                        {'params': self.wide_deep_model.lr_crep.parameters(), 'lr': 0.01}]
          self.param_dict = param_dict
          self.initial_param_dict = param_dict          
          self.optim = self.select_optimizer()
          ## latent dimension = Num parameters in last layer of deep network + embedding dimension of wide network
          self.latent_dim = self.deep_layer_sizes[-1] + self.wide_embed_dim
        
        self.loss = nn.modules.loss.MSELoss()

        self.t = 0
        self.update_freq_nn = update_freq_nn 
        self.update_freq_lr = update_freq_lr 
        self.num_epochs = num_epochs  
 
        ## y_i = transpose(z) * beta_i = reward for action i
        ## sigmas = standard deviation of the beta_i

        ## Gaussian prior for each beta_i
        self._lambda_prior = lambda_prior

        ## mean values for beta_i
        self.mu = [
            np.zeros(self.latent_dim)
            for _ in range(self.num_actions)
        ]

        ## covarianve matrix, how each latent dimensions of the representation interacts with each other
        ## initialize to identity matrix with 1/lambda_prior on the diagonals
        self.cov = [(1.0 / self.lambda_prior) * np.eye(self.latent_dim)
                    for _ in range(self.num_actions)]

        ## precision, large lambda --> less uncertainty for beta_i
        self.precision = [
            self.lambda_prior * np.eye(self.latent_dim)
            for _ in range(self.num_actions)
        ]

        # Inverse Gamma prior for each sigma2_i
        self._a0 = a0
        self._b0 = b0

        self.a = [self._a0 for _ in range(self.num_actions)]
        self.b = [self._b0 for _ in range(self.num_actions)] 


        self.data_h = ContextualDataset(self.context_dim,
                                        self.num_actions,
                                        intercept=False)
        
        self.latent_h = ContextualDataset(
                            self.latent_dim,
                            self.num_actions,
                            intercept=False,
        )
        
        self.initial_pulls = initial_pulls

        ## Keep a dictionary of users that matches user's riid to indexes between 0 and num_users
        ## Initialize dicitonary with a "dummy user" that will be used for prediction when the user has never been seen
        self.user_dict = {0:0} 
        self.current_user_size = 1
        
    def select_optimizer(self):
        """Selects optimizer. To be extended (SGLD, KFAC, etc)."""
        return torch.optim.RMSprop(self.param_dict)
    
    def assign_lr(self, reset=False):
        """
        Assign learning rates using current self.param_dict.
        If reset = True, resets learning rates using self.initial_param_dict
        """
      
        if reset:
            for i in range(len(self.initial_param_dict)):
                self.optim.param_groups[i]['lr'] = self.initial_param_dict[i]['lr']
        else:
            for i in range(len(self.param_dict)):
                self.optim.param_groups[i]['lr'] = self.param_dict[i]['lr']

    def get_representation(self, user_idx, context):
        """
        Returns the latent feature vector from the neural network.
        This vector is called z in the Google Brain paper.
        """
        context = torch.tensor(context).float()

        if self.model_type == 'deep':
          z = self.deep_model.get_representation(context)
        if self.model_type == 'wide':
          z = self.wide_model.get_representation(user_idx)
        if self.model_type == 'wide_deep':
          z = self.wide_deep_model.get_representation(user_idx, context)
          
        return z
    
    def expected_values(self, user_id, context, scale=False, method='BLR'):
        """
        Computes expected values from context. Does not consider uncertainty.
        Args:
          context: Context for which the action need to be chosen.
        Returns:
          expected reward vector.
        """
        possible_methods = ['BLR','forward']
        if method not in possible_methods:
          raise NameError('method must be "BLR",or "forward"')
        
        if not torch.is_tensor(user_id):
            user_id = torch.tensor(user_id)
        
        if scale:
            context = context.reshape(-1, self.context_dim)
            context = self.data_h.scale_contexts(contexts=context)[0]
        
        user_idx = self.lookup_one_user_idx(user_id)

        if method == 'BLR':
          ## Compute last-layer representation for the current user_id, context
          z_context = self.get_representation(user_idx, context).numpy()

          ## Compute sampled expected values, intercept is last component of beta
          vals = [np.dot(self.mu[i], z_context.T) for i in range(self.num_actions)]
        
          return np.array(vals)

        elif method == 'forward':
          ## Get expected values from network forward pass
          if self.model_type == 'deep':
            vals = self.deep_model.forward(context.float())
          if self.model_type == 'wide':
            vals = self.wide_model.forward(user_idx)
          if self.model_type == 'wide_deep':
            vals = self.wide_deep_model.forward(user_idx, context.float(), combine_method=self.wd_combine_method)
            if self.wd_combine_method == 'concat_reward':
              ## Add the output from wide and deep model
              n_act=self.hparams['num_actions'] ## Number of actions for combining wide and deep outputs
              vals = vals[0:n_act]+vals[n_act:n_act*2]
          return vals.detach().numpy() 

        
    
    def action(self, user_id, context, method='BLR_TS'):
        """Samples beta's from posterior, and chooses best action accordingly."""
        ## Possible methods to sample posterior:
        ## BLR - Expected values of Bayesian Linear Regression
        ## BLR_TS - Bayesian Linear Regression + Thompson sampling
        ## forward - forward pass of neural networks

        possible_methods = ['BLR_TS', 'BLR','forward']
        if method not in possible_methods:
          raise NameError('method must be "BLR_TS", "BLR",or "forward"')
        
        if not torch.is_tensor(user_id):
            user_id = torch.tensor(user_id)

        ## Round robin until each action has been selected "initial_pulls" times
        if self.t < self.num_actions * self.initial_pulls:
            return self.t % self.num_actions
        else:
            if self.do_scaling:
                context = context.reshape(-1, self.context_dim)
                context = self.data_h.scale_contexts(contexts=context)[0]
              
            if method == 'forward':
              ## Select an action based on expected values (from network forward pass) of reward
              vals = self.expected_values(user_id, context, method="forward")
            
            elif method == 'BLR':
              ## Select an action based on expected values of Bayesian linear regression
              vals = self.expected_values(user_id, context, method="BLR")
  
            elif method == 'BLR_TS':
              ## Select an action based on Bayesian linear regression and Thompson sampling
              vals = self._sample(user_id, context)
            
            return np.argmax(vals)

    def update(self, user_id, context, action, reward):
        """
        Args:
          context: Last observed context.
          action: Last observed action.
          reward: Last observed reward.
        """

        if not torch.is_tensor(user_id):
          user_id = torch.tensor(user_id)
        
        self.t += 1
        self.data_h.add(user_id, context, action, reward)
        self.update_user_dict(user_id)

        if self.t % self.update_freq_nn == 0:
          self.train(self.data_h, self.num_epochs)
        
        if self.t % self.update_freq_lr == 0:
          self._update_actions()   
           
        if self.t > 1:
          context = context.reshape((1, self.context_dim))
          context = self.data_h.scale_contexts(contexts=context)[0]

        user_idx = self.lookup_one_user_idx(user_id)
        z_context = self.get_representation(user_idx, context)
        self.latent_h.add(user_idx, z_context, action, reward) 

        ## Replace context representations if NN is retrained
        if self.t % self.update_freq_nn == 0: 
          self._replace_latent_h()
        
    def _update_actions(self):
        """
        Update the Bayesian Linear Regression on
        stored latent variables.
        """
        # Find all the actions to update
        actions_to_update = self.latent_h.actions

        for action_v in np.unique(actions_to_update):

            # Update action posterior with formulas: \beta | z,y ~ N(mu_q, cov_q)
            _, z, y = self.latent_h.get_data(action_v)
            z = z.numpy()
            y = y.numpy()

            # The algorithm could be improved with sequential formulas (cheaper)
            s = np.dot(z.T, z)

            # Some terms are removed as we assume prior mu_0 = 0.
            precision_a = s + self.lambda_prior * np.eye(self.latent_dim)
            cov_a = np.linalg.inv(precision_a)
            mu_a = np.dot(cov_a, np.dot(z.T, y))

            # Inverse Gamma posterior update
            a_post = self.a0 + z.shape[0] / 2.0
            b_upd = 0.5 * np.dot(y.T, y)
            b_upd -= 0.5 * np.dot(mu_a.T, np.dot(precision_a, mu_a))
            b_post = self.b0 + b_upd

            # Store new posterior distributions
            self.mu[action_v] = mu_a
            self.cov[action_v] = cov_a
            self.precision[action_v] = precision_a
            self.a[action_v] = a_post
            self.b[action_v] = b_post

    def train(self, data, num_steps):
        """Trains the network for num_steps, using the provided data.
        Args:
          data: ContextualDataset object that provides the data.
          num_steps: Number of minibatches to train the network for.
        Takes longer to get batch data and train model as the data size increase
        """
        #print("Training at time {} for {} steps...".format(self.t, num_steps))

        batch_size = self.batch_size
        
        data.scale_contexts() ## have to scale the data first if scaled=True in data.get_batch_with_weights()

        for step in range(num_steps):
            ## Use this line to randomly select a batch with size batch_size
            #u, x, y, w = data.get_batch_with_weights(batch_size, scaled=True)
            
            ## Use this line to select the most recent update_freq_nn data points 
            ## (i.e. select the most recent 100 data points if traning every 100 steps), 
            ## then randomly select the remaining data in batch
            u, x, y, w = data.get_batch_with_weights_recent(batch_size, n_recent=self.update_freq_nn, scaled=True)
            
            u = self.lookup_user_idxs(u)

            ## Training at time step 1 will cause problem if scaled=True, 
            ## because standard deviation=0, and scaled_context will equal nan
            if self.t != 1:
              self.do_step(u, x, y, w, step)
        

    def do_step(self, u, x, y, w, step):
        
        decay_rate_wide = self.lr_decay_rate_wide
        base_lr_wide = self.initial_lr_wide
        decay_rate_deep = self.lr_decay_rate_deep
        base_lr_deep = self.initial_lr_deep

        self.lr_wide = base_lr_wide * (1 / (1 + (decay_rate_wide * step)))
        self.lr_deep = base_lr_deep * (1 / (1 + (decay_rate_deep * step)))
        
        if self.model_type == 'deep':
            self.param_dict[0]['lr'] = self.lr_deep
        if self.model_type == 'wide':
            self.param_dict[0]['lr'] = self.lr_wide
        if self.model_type == 'wide_deep':
            self.param_dict[0]['lr'] = self.lr_wide
            self.param_dict[1]['lr'] = self.lr_deep
        
        self.assign_lr()
        
        if self.model_type == 'deep':
          y_hat = self.deep_model.forward(x.float())
        if self.model_type == 'wide':
          y_hat = self.wide_model.forward(u)
        if self.model_type == 'wide_deep':
          y_hat = self.wide_deep_model(u,x.float(),combine_method=self.wd_combine_method)
          if self.wd_combine_method == 'concat_reward':
            ## replicate y and w to compare with concatenated y_hat from wide_deep model if concatenating reward
            y = torch.cat((y,y), dim=1) 
            w = torch.cat((w,w), dim=1)
        
        y_hat *= w
        ls = self.loss(y_hat, y.float())
        ls.backward()

        clip = self.max_grad_norm

        if self.model_type == 'deep':
          torch.nn.utils.clip_grad_norm_(self.deep_model.parameters(), clip)
        if self.model_type == 'wide':
          torch.nn.utils.clip_grad_norm_(self.wide_model.parameters(), clip)
        if self.model_type == 'wide_deep':
          torch.nn.utils.clip_grad_norm_(self.wide_deep_model.parameters(), clip)

        self.optim.step()
        self.optim.zero_grad()

    def _replace_latent_h(self):
        # Update the latent representation of every datapoint collected so far
        if self.do_scaling:
            self.data_h.scale_contexts()
        ctx = self.data_h.get_contexts(scaled=self.do_scaling)

        user_idx = self.latent_h.get_user_ids()
        new_z = self.get_representation(user_idx, ctx)

        self.latent_h._replace_data(contexts=new_z)
    
    def _retrain_nn(self):
        """Retrain the network on original data (data_h)"""
        if self.reset_lr:
            self.assign_lr(reset=True)

        ## Uncomment following lines to automatically set number of steps according to data length and batch size.
        #steps = round(self.num_epochs * (len(self.data_h)/self.batch_size))
        #print(f"training for {steps} steps.")
        self.train(self.data_h, self.num_epochs)
        self._replace_latent_h()

    def lookup_one_user_idx(self, user_id):
      user_id = user_id.tolist()[0]
      if user_id not in self.user_dict.keys():
        #print("User not found, returning dummy user")
        user_index = 0
      else:
        user_index = self.user_dict[user_id]
      return torch.tensor(user_index)

    def lookup_user_idxs(self, user_ids):
      ## Returns a list of user indexes for input to the wide network
      user_ids = user_ids.tolist()
      user_index = [self.user_dict[u] for u in user_ids]
      return torch.tensor(user_index)
    
    def update_user_dict(self, user_id):
      ## Create/update a lookup dictionary that matches user riid to a user index between 0 and num_users
      user_id = user_id.tolist()[0]
      if user_id not in self.user_dict:
        self.user_dict.update({user_id:self.current_user_size})
        self.current_user_size += 1
    
    def _sample(self, user_id, context, parallelize=False, n_threads=-1):
        ## Sample sigma2, and beta conditional on sigma2
        #n_rows = len(context)
        d = self.mu[0].shape[0]
        #a_projected = np.repeat(np.array(self.a)[np.newaxis, :], n_rows, axis=0)
        a = np.array(self.a)
        sigma2_s = self.b * invgamma.rvs(a)
        #if n_rows == 1:
        #    sigma2_s = sigma2_s.reshape(1, -1)
        beta_s = []
        try:
            for i in range(self.num_actions):
                global mus
                global covs
                #mus = np.repeat(self.mu[i][np.newaxis, :], n_rows, axis=0)
                mus = self.mu[i]
                s2s = sigma2_s[i]
                rep = np.repeat(s2s, d)
                rep = np.repeat(rep[:, np.newaxis], d, axis=1)
                #rep = np.repeat(rep[:, :, np.newaxis], d, axis=2)
                #covs = np.repeat(self.cov[i][np.newaxis, :, :], n_rows, axis=0)
                covs = self.cov[i]
                covs = rep * covs
                if parallelize:
                    multivariates = parallelize_multivar(mus, covs, n_threads=n_threads)
                else:
                    multivariates = [np.random.multivariate_normal(mus, covs)]
                beta_s.append(multivariates)
        except np.linalg.LinAlgError as e:
            ## Sampling could fail if covariance is not positive definite
            print('Exception when sampling from {}.'.format(self.name))
            print('Details: {} | {}.'.format(e.message, e.args))
            for i in range(self.num_actions):
                multivariates = [np.random.multivariate_normal(np.zeros((d)), np.eye(d))]
                beta_s.append(multivariates)
        beta_s = np.array(beta_s)

        ## Compute last-layer representation for the current context
        user_idx = self.lookup_one_user_idx(user_id)
        z_context = self.get_representation(user_idx, context).numpy()

        ## Apply Thompson Sampling
        vals = [
            (beta_s[i, :, :] * z_context).sum(axis=-1)
            for i in range(self.num_actions)
        ]
        return np.array(vals)
              
    def save(self, path):
        """saves model to path"""
        with open(path, 'wb') as f:
            pickle.dump(self, f)

    @property
    def a0(self):
        return self._a0

    @property
    def b0(self):
        return self._b0

    @property
    def lambda_prior(self):
        return self._lambda_prior