#!/usr/bin/env python3

import torch
from .cholesky import cholesky_solve
from .. import settings


def woodbury_factor(umat, vmat, diag):
    r"""
    Given a matrix factorized as (D + U V^T), where
    U, V are (n x k) and D is a (n x n) diagonal matrix,
    returns the matrix R so that

    .. math::

        \begin{equation*}
            R = (I_k + V^T D^-1 U)^{-1} V^T
        \end{equation*}

    to be used in solves with (D + U V^T) via the Woodbury formula.
    Can also be used in batch mode, where U, V, and D are batches of matrices

    Args: 
        :attr:`umat` (Tensor n x k):
            The left matrix factor
        :attr:`vmat` (Tensor n x k):
            The right matrix factor
        :attr:`diag` (Tensor n):
            The diagonal of D

    """
    if settings.debug.on():
        if umat.shape != vmat.shape:
            raise ValueError("umat ({}) and vmat ({}) must have the same shape.".format(umat.shape, vmat.shape))
        if umat.shape[:-1] != diag.shape:
            raise ValueError("Incompatible shape for diag ({}) given umat shape ({}).".format(diag.shape, umat.shape))

    # Sizes
    *batch_shape, n, k = umat.shape

    # These reshapes make it easier to use faster blas calls
    umat = umat.view(-1, n, k)
    vmat = vmat.view(-1, n, k)
    diag = diag.view(-1, n, 1)

    # Scale the diagonal
    # s = scale = max |1 / diag|
    inv_scale = diag.abs().min().reciprocal()
    scaled_inv_diag = inv_scale / diag

    # Compute (1/s (I_k + V^T D^-1 U)), where s is a scale factor
    inner_mat = torch.baddbmm(
        inv_scale,
        torch.eye(k, dtype=scaled_inv_diag.dtype, device=scaled_inv_diag.device),
        1,
        vmat.transpose(-1, -2),
        umat * scaled_inv_diag
    )

    # Compute s (I_k + V^T D^-1 U))^-1 V^T
    R = cholesky_solve(vmat.transpose(-1, -2), torch.cholesky(inner_mat))
    return R.view(*batch_shape, k, n)


def woodbury_solve(rhs, umat, woodbury_factor, diag):
    """
    Solves the system of equations: :math:`(D + U V^T)x = b` using the Woodbury formula,
    where x is the right-hand-side (size n), U, V are (n x k), and D is a (n x n) diagonal matrix.
    Can also be used in batch mode, where U, V, and D are batches of matrices and rhs is a batch of right-hand-side.

    This should be used after calling woodbury_factor.

    Args:
        :attr:`rhs` (size n x t)
            Right hand side vector b to solve with.
        :attr:`umat` (n x k)
            The U matrix
        :attr:`woodbury_factor` (n x k)
            The result of calling woodbury_factor on U, V, and D.
        :attr:`diag` (vector)
            The diagonal of D
    """
    # Sizes
    *batch_shape, n, k = umat.shape
    t = rhs.size(-1)

    # These reshapes make it easier to use faster blas calls
    # Scale the diagonal (using the same scale from woodbury factor)
    # s = scale = max |1 / diag|
    # E^-1 = 1/s D^-1
    inv_scale = diag.abs().min().reciprocal()
    scaled_inv_diag = inv_scale / diag.unsqueeze(-1)

    # (D + UV^T)x = D^-1 x - D^-1 U ((I + V^T D^-1 U)^-1 V^T) D^-1 x
    #             = D^-1 x - D^-1 U (1/s (woodbury_factor)) D^-1 x
    #             = s( E^-1 x - E^-1 U (woodbury_factor) E^-1 x )
    scaled_inv_diag_rhs = rhs * scaled_inv_diag
    res = torch.addcmul(scaled_inv_diag_rhs, -1, scaled_inv_diag, umat @ woodbury_factor @ scaled_inv_diag_rhs)
    res = res.div_(inv_scale)

    # Reshape the result to be the correct shape
    res = res.view(*batch_shape, n, t)
    return res