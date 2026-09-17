"""Pip entry point for the Hermes Microsoft Foundry MAI image plugin."""

from .provider import MAIImageProvider, register

__all__ = ["MAIImageProvider", "register"]
