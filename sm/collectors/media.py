from __future__ import annotations

import shutil

from . import Collector, register
from ..media import Media


@register
class MediaPlaying(Collector):
    """O que está tocando (MPRIS: Spotify, navegador, VLC...)."""
    name = "media"
    interval = 2

    def setup(self) -> None:
        if not shutil.which("busctl"):
            raise RuntimeError("busctl não encontrado")
        self.media = Media()
        self.ctx.media = self.media      # usado pela API (controles e capa)

    def collect(self) -> dict:
        return self.media.collect()
