"""Tracking da cabeça: posição 3D dos olhos de até 3 pessoas, em cm.

Cada câmera master detecta rostos (YuNet, do OpenCV). Para cada rosto:
  - distância Z = k_face / largura_do_rosto_em_px  (k_face vem da calibração:
    a pessoa fica a 100 cm e a 200 cm e medimos a largura; sem calibração usa
    um valor padrão razoável para webcam de ~65° de campo);
  - ponto entre os olhos (landmarks 0 e 1) -> raio da câmera -> ponto 3D;
  - câmera -> mundo pela posição (cm) e pelos ângulos yaw/pitch de cameras.json.

Sistema de coordenadas (o mesmo de screens.json): origem no chão, no centro da
base da TV 32"; x para a direita de quem observa, y para cima, z para a frente
(na direção da pessoa). Uma câmera com yaw 0 e pitch 0 olha para +z.

Filtros contra rostos falsos (estampas, cartazes, bonecos): faixa de distância
plausível; rosto que não se move nem 1,5 px por 20 s vira "estático" e é
ignorado naquele lugar; pessoa só passa a contar depois de N s presente.

Uso direto (teste): python tools/tracking_teste.py
"""
import math
import threading
import time
from pathlib import Path

import cv2
import numpy as np

RAIZ = Path(__file__).resolve().parents[2]
MODELO = RAIZ / "models" / "face_detection_yunet_2023mar.onnx"

LARGURA_ROSTO_CM = 15.0      # só para converter k_face em distância focal
HFOV_PADRAO = 65.0           # campo horizontal presumido de webcam barata


# ---------------------------------------------------------------- geometria
def base_camera(yaw_graus, pitch_graus):
    """Vetores (direita, cima, frente) da câmera no mundo.

    yaw positivo aponta a câmera para +x (a direita de quem observa); é a
    mesma convenção de screens.json, onde a tela da esquerda tem yaw positivo
    para virar a face para o centro. pitch positivo inclina para baixo.
    """
    # câmera "neutra" olha para +z (para a pessoa): a direita da imagem é -x
    r = np.array([-1.0, 0.0, 0.0])
    u = np.array([0.0, 1.0, 0.0])
    f = np.array([0.0, 0.0, 1.0])
    p = math.radians(pitch_graus)
    # pitch: gira em torno do eixo da direita; frente desce para -y
    f2 = f * math.cos(p) - u * math.sin(p)
    u2 = u * math.cos(p) + f * math.sin(p)
    y = math.radians(yaw_graus)
    c, s = math.cos(y), math.sin(y)

    def gira_y(v):  # rotação em torno de +y (positivo = anti-horário visto de cima)
        return np.array([c * v[0] + s * v[2], v[1], -s * v[0] + c * v[2]])

    return gira_y(r), gira_y(u2), gira_y(f2)


class ModeloCamera:
    def __init__(self, entrada, largura=640, altura=480):
        self.nome = entrada["nome"]
        self.w, self.h = largura, altura
        self.cx, self.cy = largura / 2, altura / 2
        fx_padrao = (largura / 2) / math.tan(math.radians(HFOV_PADRAO / 2))
        self.k_face = entrada.get("k_face") or fx_padrao * LARGURA_ROSTO_CM
        self.fx = entrada.get("fx") or self.k_face / LARGURA_ROSTO_CM
        pos = entrada.get("posicao_cm") or [0, 165, 5]
        self.pos = np.array(pos, dtype=float)
        self.yaw = float(entrada.get("yaw", 0.0) or 0.0)
        self.pitch = float(entrada.get("pitch", 10.0) if entrada.get("pitch") is not None else 10.0)
        self.r, self.u, self.f = base_camera(self.yaw, self.pitch)

    def ponto(self, u_px, v_px, largura_px):
        z = self.k_face / max(largura_px, 1.0)
        xc = (u_px - self.cx) * z / self.fx
        yc = -(v_px - self.cy) * z / self.fx
        return self.pos + xc * self.r + yc * self.u + z * self.f, z


# ---------------------------------------------------------------- detecção
class Deteccao:
    __slots__ = ("cam", "caixa", "olhos_px", "largura_px", "conf", "p", "dist", "frame")

    def __init__(self, cam, caixa, olhos_px, largura_px, conf, p, dist, frame=None):
        self.cam, self.caixa, self.olhos_px = cam, caixa, olhos_px
        self.largura_px, self.conf, self.p, self.dist = largura_px, conf, p, dist
        self.frame = frame          # quadro de onde saiu (para recortar o rosto: analise.py)


class FiltroEstatico:
    """Marca como imagem (não gente) o rosto que fica parado no mesmo pixel."""

    def __init__(self, tolerancia_px=4.0, tolerancia_largura=0.12, segundos=20.0, esquecer_s=60.0):
        # Medido em 05/11: o rosto de uma estampa, com pouca luz, oscila ~3 px
        # de posição e ~10% de largura entre quadros. A comparação é sempre com
        # a posição em que o candidato apareceu, não com o quadro anterior.
        self.tol, self.tol_w, self.seg, self.esq = tolerancia_px, tolerancia_largura, segundos, esquecer_s
        self.candidatos = []   # [cx, cy, w, t_inicio, t_visto]
        self.estaticos = []    # [cx, cy, w, t_visto]

    def _perto(self, a, cx, cy, w, tol):
        return abs(a[0] - cx) <= tol and abs(a[1] - cy) <= tol and abs(a[2] - w) <= self.tol_w * a[2]

    def filtrar(self, caixas, agora):
        self.estaticos = [e for e in self.estaticos if agora - e[3] < self.esq]
        self.candidatos = [c for c in self.candidatos if agora - c[4] < 1.0]
        boas = []
        for cx, cy, w, extra in caixas:
            est = next((e for e in self.estaticos if self._perto(e, cx, cy, w, self.tol * 2)), None)
            if est:
                est[3] = agora
                continue
            cand = next((c for c in self.candidatos if self._perto(c, cx, cy, w, self.tol)), None)
            if cand is None:
                self.candidatos.append([cx, cy, w, agora, agora])
            else:
                cand[4] = agora
                if agora - cand[3] > self.seg:
                    self.estaticos.append([cx, cy, w, agora])
                    self.candidatos.remove(cand)
                    continue
            boas.append(extra)
        return boas


# ---------------------------------------------------------------- pessoas
class Pessoa:
    _proximo_id = 1

    def __init__(self, p, agora):
        self.id = Pessoa._proximo_id
        Pessoa._proximo_id += 1
        self.p = np.array(p, dtype=float)       # olhos, cm, suavizado
        self.v = np.zeros(3)
        self.t_criada = agora
        self.t_vista = agora
        self.confirmada = False
        self.cams = set()
        self.det = None             # melhor detecção recente (maior rosto entre as câmeras)

    @property
    def idade(self):
        return self.t_vista - self.t_criada

    def atualizar(self, p, agora, alfa):
        dt = max(agora - self.t_vista, 1e-3)
        novo = self.p + alfa * (np.asarray(p) - self.p)
        self.v = 0.7 * self.v + 0.3 * (novo - self.p) / dt
        self.p = novo
        self.t_vista = agora

    def como_dict(self):
        return {"id": self.id, "x": float(self.p[0]), "y": float(self.p[1]), "z": float(self.p[2]),
                "confirmada": self.confirmada, "cams": sorted(self.cams),
                "segundos": round(time.time() - self.t_criada, 1)}


class Tracker:
    def __init__(self, cfg, masters, mostrar_debug=False):
        t = cfg["app"].get("tracking", {})
        self.entrar_s = t.get("segundos_para_entrar", 2.0)
        self.sair_s = t.get("segundos_para_sair", 5.0)
        self.max_pessoas = t.get("max_pessoas", 3)
        self.dmin, self.dmax = t.get("distancia_min_cm", 60), t.get("distancia_max_cm", 250)
        self.conf_min = t.get("confianca_min", 0.6)
        self.alfa = t.get("suavizacao", 0.35)
        self.raio_mesma = t.get("raio_mesma_pessoa_cm", 45)
        self.masters = masters
        entradas = {c["nome"]: c for c in cfg["cameras"].get("cameras", [])}
        cap = cfg["app"]["captura"]
        self.modelos = {m.nome: ModeloCamera(entradas.get(m.nome, {"nome": m.nome}), cap["largura"], cap["altura"])
                        for m in masters}
        self.detector = {m.nome: cv2.FaceDetectorYN.create(str(MODELO), "", (cap["largura"], cap["altura"]),
                                                             self.conf_min, 0.3, 10) for m in masters}
        self.estatico = {m.nome: FiltroEstatico(**t.get("filtro_estatico", {})) for m in masters}
        self.pessoas = []
        self.ultimas_deteccoes = {m.nome: [] for m in masters}
        self.ignoradas = {m.nome: [] for m in masters}
        self._lock = threading.Lock()
        self._rodando = False
        self._ultimo_quadro = {}
        self.hz = 0.0

    # ---------- API ----------
    def iniciar(self):
        self._rodando = True
        threading.Thread(target=self._loop, name="tracker", daemon=True).start()
        return self

    def parar(self):
        self._rodando = False

    def estado(self):
        """Lista de pessoas confirmadas (dicts), mais próxima da tela primeiro."""
        with self._lock:
            ps = [p.como_dict() for p in self.pessoas if p.confirmada]
        return sorted(ps, key=lambda d: d["z"])

    def rosto(self, pid):
        """Melhor visão recente do rosto da pessoa: {frame, caixa, cam} ou None (usado por analise.py)."""
        with self._lock:
            p = next((q for q in self.pessoas if q.id == pid), None)
            d = p.det if p is not None else None
            if d is None or d.frame is None:
                return None
            return {"frame": d.frame, "caixa": d.caixa, "cam": d.cam}

    def todas(self):
        with self._lock:
            return [p.como_dict() for p in self.pessoas]

    # ---------- interno ----------
    def detectar(self, nome, f):
        h, w = f.shape[:2]
        det = self.detector[nome]
        det.setInputSize((w, h))
        _, faces = det.detect(f)
        if faces is None:
            return [], []
        mod = self.modelos[nome]
        cruas = []
        ruins = []
        for fc in faces:
            x, y, ww, hh = fc[:4]
            olho_a, olho_b = fc[4:6], fc[6:8]
            meio = (olho_a + olho_b) / 2
            p, dist = mod.ponto(float(meio[0]), float(meio[1]), float(ww))
            d = Deteccao(nome, (int(x), int(y), int(ww), int(hh)), (float(meio[0]), float(meio[1])),
                         float(ww), float(fc[14]), p, dist, f)
            if not (self.dmin <= dist <= self.dmax):
                ruins.append(d)
                continue
            cruas.append((float(x + ww / 2), float(y + hh / 2), float(ww), d))
        boas = self.estatico[nome].filtrar(cruas, time.time())
        ruins += [c[3] for c in cruas if c[3] not in boas]
        return boas, ruins

    def _fundir(self, deteccoes):
        """Junta detecções de câmeras diferentes que são a mesma pessoa."""
        grupos = []
        for d in sorted(deteccoes, key=lambda d: -d.largura_px):
            g = next((g for g in grupos if np.linalg.norm(g["p"] - d.p) < self.raio_mesma
                      and d.cam not in g["cams"]), None)
            if g is None:     # a primeira do grupo é a de maior rosto (ordem acima)
                grupos.append({"p": d.p.copy(), "peso": d.largura_px, "cams": {d.cam}, "melhor": d})
            else:
                tot = g["peso"] + d.largura_px
                g["p"] = (g["p"] * g["peso"] + d.p * d.largura_px) / tot
                g["peso"] = tot
                g["cams"].add(d.cam)
        return grupos

    def _associar(self, grupos, agora):
        livres = list(self.pessoas)
        for g in sorted(grupos, key=lambda g: -g["peso"]):
            melhor, dmin = None, 80.0
            for p in livres:
                d = np.linalg.norm(p.p - g["p"])
                if d < dmin:
                    melhor, dmin = p, d
            if melhor is not None:
                melhor.atualizar(g["p"], agora, self.alfa)
                melhor.cams = g["cams"]
                melhor.det = g["melhor"]
                livres.remove(melhor)
            elif len(self.pessoas) < self.max_pessoas + 2:   # folga para candidatas
                np_ = Pessoa(g["p"], agora)
                np_.cams = g["cams"]
                np_.det = g["melhor"]
                self.pessoas.append(np_)
        for p in self.pessoas:
            if not p.confirmada and p.idade >= self.entrar_s:
                confirmadas = sum(1 for q in self.pessoas if q.confirmada)
                if confirmadas < self.max_pessoas:
                    p.confirmada = True
        limite = lambda p: self.sair_s if p.confirmada else 0.6
        self.pessoas = [p for p in self.pessoas if agora - p.t_vista < limite(p)]

    def _loop(self):
        n, t_n = 0, time.time()
        while self._rodando:
            novas = False
            todas = []
            for m in self.masters:
                f, idade = m.quadro()
                if f is None or idade > 1.0:
                    self.ultimas_deteccoes[m.nome], self.ignoradas[m.nome] = [], []
                    continue
                if self._ultimo_quadro.get(m.nome) is f:
                    todas += self.ultimas_deteccoes[m.nome]
                    continue
                self._ultimo_quadro[m.nome] = f
                novas = True
                boas, ruins = self.detectar(m.nome, f)
                self.ultimas_deteccoes[m.nome], self.ignoradas[m.nome] = boas, ruins
                todas += boas
            if not novas:
                time.sleep(0.005)
                continue
            agora = time.time()
            with self._lock:
                self._associar(self._fundir(todas), agora)
            n += 1
            if agora - t_n >= 1:
                self.hz, n, t_n = n / (agora - t_n), 0, agora
