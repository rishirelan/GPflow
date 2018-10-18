import numpy as np
import tensorflow as tf

from . import dispatch
from .. import kernels
from .. import mfn as mfn
from ..covariances import Kuf
from ..features import InducingFeature
from ..quadrature import mvnquad
from ..util import NoneType, create_logger
from .expectations import quadrature_expectation
from .probability_distributions import (DiagonalGaussian, Gaussian,
                                        MarkovGaussian)

logger = create_logger()
register = dispatch.quadrature_expectation.register


def get_eval_func(obj, feature, slice=None):
    """
    Return the function of interest (kernel or mean) for the expectation
    depending on the type of :obj: and whether any features are given
    """

    slice = ... if slice is None else slice
    if feature is not None:
        # kernel + feature combination
        if not isinstance(feature, InducingFeature) or not isinstance(obj, kernels.Kernel):
            raise TypeError("If `feature` is supplied, `obj` must be a kernel.")
        return lambda x: tf.transpose(Kuf(feature, obj, x))[slice]
    elif isinstance(obj, mfn.MeanFunction):
        return lambda x: obj(x)[slice]
    elif isinstance(obj, kernels.Kernel):
        return obj

    raise NotImplementedError()


@register((Gaussian, DiagonalGaussian),
          object, (InducingFeature, NoneType),
          object, (InducingFeature, NoneType),
          (int, NoneType))
def _quadrature_expectation(p, obj1, feature1, obj2, feature2, nghp=None):
    """
    General handling of quadrature expectations for Gaussians and DiagonalGaussians
    Fallback method for missing analytic expectations
    """
    nghp = 100 if nghp is None else nghp

    logger.warn("Quadrature is used to calculate the expectation. This means that "
                "an analytical implementations is not available for the given combination.")

    if obj1 is None:
        raise NotImplementedError("First object cannot be None.")

    if obj2 is None:
        def eval_fun(x):
            return get_eval_func(obj1, feature1)(x)
    else:
        def eval_func(x):
            res1 = get_eval_func(obj1, feature1, np.s_[:, :, None])(x)
            res2 = get_eval_func(obj2, feature2, np.s_[:, None, :])(x)
            return res1 * res2

    if isinstance(p, DiagonalGaussian):
        iskernel1 = isinstance(obj1, kernels.Kernel)
        iskernel2 = isinstance(obj2, kernels.Kernel)
        separate_dims = obj1.on_separate_dims(obj2)
        if iskernel1 and iskernel2 and separate_dims:  # no joint expectations required
            eKxz1 = quadrature_expectation(p, (obj1, feature1), nghp=nghp)
            eKxz2 = quadrature_expectation(p, (obj2, feature2), nghp=nghp)
            return eKxz1[:, :, None] * eKxz2[:, None, :]

        else:
            cov = tf.matrix_diag(p.cov)
    else:
        cov = p.cov
    return mvnquad(eval_func, p.mu, cov, nghp)


@register(MarkovGaussian,
          object, (InducingFeature, NoneType),
          object, (InducingFeature, NoneType),
          (int, NoneType))
def _quadrature_expectation(p, obj1, feature1, obj2, feature2, nghp=None):
    """
    Handling of quadrature expectations for Markov Gaussians (useful for time series)
    Fallback method for missing analytic expectations wrt Markov Gaussians
    Nota Bene: obj1 is always associated with x_n, whereas obj2 always with x_{n+1}
               if one requires e.g. <x_{n+1} K_{x_n, Z}>_p(x_{n:n+1}), compute the
               transpose and then transpose the result of the expectation
    """
    nghp = 40 if nghp is None else nghp

    logger.warn("Quadrature is used to calculate the expectation. This means that "
                "an analytical implementations is not available for the given combination.")

    if obj2 is None:
        def eval_func(x):
            return get_eval_func(obj1, feature1)(x)
        mu, cov = p.mu[:-1], p.cov[0, :-1]  # cross covariances are not needed
    elif obj1 is None:
        def eval_func(x):
            return get_eval_func(obj2, feature2)(x)
        mu, cov = p.mu[1:], p.cov[0, 1:]  # cross covariances are not needed
    else:
        def eval_func(x):
            x1 = tf.split(x, 2, 1)[0]
            x2 = tf.split(x, 2, 1)[1]
            res1 = get_eval_func(obj1, feature1, np.s_[:, :, None])(x1)
            res2 = get_eval_func(obj2, feature2, np.s_[:, None, :])(x2)
            return res1 * res2

        mu = tf.concat((p.mu[:-1, :], p.mu[1:, :]), 1)  # Nx2D
        cov_top = tf.concat((p.cov[0, :-1, :, :], p.cov[1, :-1, :, :]), 2)  # NxDx2D
        cov_bottom = tf.concat((tf.matrix_transpose(p.cov[1, :-1, :, :]), p.cov[0, 1:, :, :]), 2)
        cov = tf.concat((cov_top, cov_bottom), 1)  # Nx2Dx2D

    return mvnquad(eval_func, mu, cov, nghp)
