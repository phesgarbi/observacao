"""Quem está em cada lado da ilha, quem manda nas telas de cada lado e o que ela vê.

Montagem em ilha (desde 11/11): mesa no centro, telas costas com costas, o
público circula pelas duas laterais. Cada lado tem duas zonas (ilha.json):

    perto  (até a fita no chão, ~1,2 m)  quem está aqui pode virar "dono" do lado:
                                          as telas daquele lado viram janela para ele.
    longe  (até ~3 m)                     só registro: aparece nas fotos e no mural.

Regras por lado (decisões da reunião de 10/11, adaptadas para a ilha):
    ninguém na zona perto        -> "vazio": as telas mostram a grade das câmeras ao vivo
                                    (parede de monitores; decisão de 11/11).
    1 ou mais na zona perto      -> "pessoa": o mais perto da mesa é o dono (com histerese).
         primeiros ver_se_s s    -> o dono se vê ao vivo (as câmeras agora).
         depois                  -> vê quem é dono do OUTRO lado da mesa (se houver),
                                    senão a pessoa anterior; troca a cada troca_s s.
         após alerta_s s         -> flag "alerta" (aviso de consumo excessivo + falha visual).
    lotacao ou mais na perto     -> "arquivo": aquele lado vira mural.

Sessões do banco: abrem só para quem entra na zona perto e fecham depois de
sair_s s fora dela. Quem fica só na zona longe não vira sessão.
A 32" (lado "frente") é um painel 2D: não tem dono.
"""
import random
import time

from .ilha import LADOS

MIN_QUADROS_OUTRO = 8     # sessão em andamento com menos que isso ainda não serve para mostrar
OUTRO = {"esq": "dir", "dir": "esq"}


class EstadoIlha:
    def __init__(self, cfg, banco, ilha, seed=5):
        self.ilha, self.banco = ilha, banco
        c = ilha.comportamento
        self.ver_se_s = float(c.get("ver_se_s", 10))
        self.troca_s = float(c.get("troca_s", 35))
        self.alerta_s = float(c.get("alerta_s", 300))
        self.sair_s = float(c.get("sair_s", 3))
        self.lotacao = int(c.get("lotacao", 4))
        self.troca_dono_cm = float(c.get("troca_dono_cm", 30))
        self.rng = random.Random(seed)
        self.lados = {l: {"dono_id": None, "t_dono": None, "alvo": None, "t_alvo": 0.0, "fase": "vazio"}
                      for l in LADOS}
        self._fora = {}           # pessoa id -> desde quando está fora da zona perto
        self.quantas = 0

    # ---------- banco
    def _sessoes(self, pessoas_perto, agora):
        ids = {q["id"] for q in pessoas_perto}
        for q in pessoas_perto:
            self.banco.abrir(q["id"])
            self._fora.pop(q["id"], None)
        for pid in list(self.banco.gravando):
            if pid in ids:
                continue
            t = self._fora.setdefault(pid, agora)
            if agora - t >= self.sair_s:
                self.banco.encerrar(pid)
                self._fora.pop(pid, None)

    def _outra_sessao(self, excluir):
        """Uma sessão fechada qualquer, diferente das excluídas (para a troca de perspectiva)."""
        todas = [s for s in self.banco.todas() if s not in excluir]
        if not todas:
            return None
        recentes = todas[-12:]                      # privilegia quem passou há pouco
        return self.rng.choice(recentes)

    # ---------- principal
    def atualizar(self, pessoas):
        """pessoas = Tracker.estado() (dicts com id, x, y, z em cm). Devolve o que cada lado mostra."""
        agora = time.time()
        todas = []
        for p in pessoas:
            lado, d, zona = self.ilha.classificar((p["x"], p["y"], p["z"]))
            todas.append({**p, "lado": lado, "dist": d, "zona": zona})
        perto = [q for q in todas if q["zona"] == "perto"]
        self.quantas = len(todas)
        self._sessoes(perto, agora)

        donos = {}
        for lado in LADOS:
            st = self.lados[lado]
            aqui = sorted((q for q in perto if q["lado"] == lado), key=lambda q: q["dist"])
            dono = None
            if len(aqui) >= self.lotacao:
                st["fase"] = "arquivo"
            elif aqui:
                atual = next((q for q in aqui if q["id"] == st["dono_id"]), None)
                if atual is not None and aqui[0]["dist"] > atual["dist"] - self.troca_dono_cm:
                    dono = atual                    # histerese: só perde a tela para alguém bem mais perto
                else:
                    dono = aqui[0]
            if dono is None:
                st["dono_id"], st["t_dono"] = None, None
            elif dono["id"] != st["dono_id"]:
                st["dono_id"], st["t_dono"] = dono["id"], agora
                st["alvo"], st["t_alvo"] = None, 0.0
            donos[lado] = dono

        saida = {}
        for lado in LADOS:
            st, dono = self.lados[lado], donos[lado]
            info = {"dono": dono, "segundos": 0.0, "alerta": False,
                    "perto": sum(1 for q in perto if q["lado"] == lado),
                    "longe": sum(1 for q in todas if q["lado"] == lado and q["zona"] == "longe")}
            if info["perto"] >= self.lotacao:
                info.update(modo="arquivo", fase="arquivo", alvo=None)
            elif dono is None:
                st["fase"] = "vazio"
                info.update(modo="vazio", fase="vazio", alvo=None)
            else:
                t = agora - st["t_dono"]
                propria = self.banco.gravando.get(dono["id"])
                outro = donos[OUTRO[lado]]
                sess_outro = self.banco.gravando.get(outro["id"]) if outro else None
                if t < self.ver_se_s:
                    fase, alvo = "espelho", None                    # None = câmeras ao vivo: você agora
                elif sess_outro is not None and sess_outro.total() >= MIN_QUADROS_OUTRO:
                    fase, alvo = "outro_lado", sess_outro           # quem está do outro lado da mesa
                else:
                    fase = "anterior"
                    alvo = st["alvo"] if st["fase"] == "anterior" else None
                    if alvo is None:
                        alvo = self.banco.anterior(excluir=[propria])
                        st["t_alvo"] = agora
                    elif agora - st["t_alvo"] >= self.troca_s:
                        alvo = self._outra_sessao([propria, alvo]) or alvo
                        st["t_alvo"] = agora
                st["fase"], st["alvo"] = fase, alvo
                info.update(modo="pessoa", fase=fase, alvo=alvo, propria=propria, segundos=t,
                            alerta=t >= self.alerta_s)
            saida[lado] = info

        telas = {t["id"]: t["lado"] for t in self.ilha.telas}
        return {"lados": saida, "pessoas": todas, "telas": telas,
                "alerta": any(l["alerta"] for l in saida.values())}
