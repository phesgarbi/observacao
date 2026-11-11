"""Banco de frases: as da pesquisa (com fonte) e as descritivas (sobre quem está ali).

Arquivos .txt em config/frases/: uma frase por linha, fonte entre colchetes
no fim ("... [NIST 2019]"). Linhas com # são comentário.

Descritivas: montadas na hora com o que o sistema sabe da pessoa, sem
nenhum modelo de idade ou emoção (isso entra depois, se der tempo).
"""
import random
import re
import time

from .config import RAIZ


def carregar(cfg):
    pastas = cfg["app"].get("frases", {}).get("pastas", ["config/frases"])
    frases = []
    for p in pastas:
        for arq in sorted((RAIZ / p).glob("*.txt")):
            for l in arq.read_text(encoding="utf-8").splitlines():
                l = l.strip()
                if not l or l.startswith("#"):
                    continue
                m = re.match(r"(.*?)\s*\[([^\]]+)\]\s*$", l)
                frases.append((m.group(1), m.group(2)) if m else (l, ""))
    return frases


def _hora(t):
    return time.strftime("%H:%M:%S", time.localtime(t))


def descritivas(pessoa, banco, n_cameras):
    """Frases sobre a pessoa na frente da tela. pessoa = item de Estado.atualizar()['pessoas']."""
    propria = pessoa.get("propria")
    alvo = pessoa.get("alvo")
    num = propria.numero if propria else banco.total_pessoas()
    out = [
        (f"Pessoa {num:03d}. Registrada às {_hora(propria.inicio if propria else time.time())}.", "sistema"),
        (f"Você está a {pessoa.get('dist', pessoa['z']):.0f} cm da tela.", "sistema"),
        (f"Altura dos olhos: {pessoa['y']:.0f} cm.", "sistema"),
        (f"Em frente às câmeras há {pessoa['segundos']:.0f} segundos.", "sistema"),
        (f"{n_cameras} câmeras estão olhando para você agora.", "sistema"),
        ("O que você vê é a pessoa anterior. A próxima vai ver você.", "sistema"),
    ]
    if propria:
        out.append((f"{propria.total()} imagens suas guardadas até agora.", "sistema"))
    if alvo is not None and alvo is not propria:
        if alvo.fim:
            min_ = max(1, int((time.time() - alvo.fim) / 60))
            out.append((f"Você está vendo a pessoa {alvo.numero:03d}. Ela saiu daqui há {min_} min.", "sistema"))
        else:
            out.append((f"Você está vendo a pessoa {alvo.numero:03d}. Ela está do seu lado.", "sistema"))
    return out


class Sorteio:
    """Escolhe frases sem repetir até acabar o baralho."""

    def __init__(self, frases, seed=None):
        self.frases = list(frases)
        self.rng = random.Random(seed)
        self.baralho = []

    def proxima(self):
        if not self.frases:
            return ("", "")
        if not self.baralho:
            self.baralho = self.frases[:]
            self.rng.shuffle(self.baralho)
        return self.baralho.pop()
