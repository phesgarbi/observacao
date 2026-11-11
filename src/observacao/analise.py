"""Palpites do sistema sobre quem é dono de cada lado: idade, humor, altura, peso, roupa.

Decisão de 10/11 (reunião) e 11/11 (Gui): a 32" mostra uma ficha "invasiva" com
o que o sistema acha de quem está ali. É PALPITE, e a obra mostra assim: errar
faz parte (a pesquisa: "quando o sistema erra, não há a quem reclamar").

De onde vem cada palpite (sobre o melhor recorte de rosto que o tracking achou):
    idade   modelo genderage do InsightFace (buffalo_s, 2021; só a saída de idade é
            usada, a de gênero é ignorada de propósito): número contínuo de anos.
    humor   modelo emotion-ferplus-8 (ONNX, onnx/models, FER+): 8 expressões.
    altura  sem modelo: altura dos olhos medida pelo tracking + 11 cm.
    peso    sem modelo confiável possível: IMC chutado pela proporção do rosto
            (largo/estreito) × altura². Assumidamente inventado.
    roupa   cor dominante logo abaixo do rosto.

Privacidade (dossiê 07): nada daqui vai para o disco. Os palpites ficam só na
memória, ligados ao número da pessoa na noite, e somem 60 s depois de ela sair.
Não há reconhecimento nem identificação.

Sem câmeras de tracking (simulador), o rosto vem do maior rosto encontrado em
qualquer câmera (no Mac com --webcam, é você), e vale para todos os donos.
"""
import math
import threading
import time
from pathlib import Path

import cv2
import numpy as np

RAIZ = Path(__file__).resolve().parents[2]
MODELO_IDADE = RAIZ / "models" / "genderage_insightface.onnx"   # InsightFace buffalo_s (uso não comercial)
MODELO_HUMOR = RAIZ / "models" / "emotion-ferplus-8.onnx"
MODELO_ROSTO = RAIZ / "models" / "face_detection_yunet_2023mar.onnx"

HUMORES = ["neutro", "feliz", "surpreso", "triste", "bravo", "com nojo", "com medo", "desdenhoso"]
OLHOS_AO_TOPO_CM = 11.0       # dos olhos ao topo da cabeça, em média
ESQUECER_S = 60.0
SUAVE = 0.35                  # quanto cada nova análise pesa na média (0..1)


def _softmax(v):
    v = np.asarray(v, dtype=np.float64).ravel()
    if np.all(v >= 0) and abs(v.sum() - 1) < 1e-3:
        return v
    e = np.exp(v - v.max())
    return e / e.sum()


def recorte(frame, caixa, folga):
    """Caixa do rosto ampliada por `folga` (fração do tamanho), cortada nos limites da imagem."""
    x, y, w, h = caixa
    H, W = frame.shape[:2]
    dx, dy = int(w * folga), int(h * folga)
    x0, y0 = max(0, x - dx), max(0, y - dy)
    x1, y1 = min(W, x + w + dx), min(H, y + h + dy)
    if x1 - x0 < 16 or y1 - y0 < 16:
        return None
    return frame[y0:y1, x0:x1]


def regiao_roupa(frame, caixa):
    """Peito: logo abaixo do queixo, da largura dos ombros."""
    x, y, w, h = caixa
    H, W = frame.shape[:2]
    x0, x1 = max(0, int(x - 0.3 * w)), min(W, int(x + 1.3 * w))
    y0, y1 = min(H, int(y + 1.35 * h)), min(H, int(y + 2.8 * h))
    if x1 - x0 < 10 or y1 - y0 < 10:
        return None
    return frame[y0:y1, x0:x1]


def nome_cor(bgr):
    """Nome em português de uma cor BGR (cor de roupa, não de pele)."""
    hsv = cv2.cvtColor(np.uint8([[bgr]]), cv2.COLOR_BGR2HSV)[0, 0]
    h, s, v = int(hsv[0]) * 2, int(hsv[1]), int(hsv[2])      # h em graus
    if v < 55:
        return "preta"
    if s < 40:
        return "branca" if v > 190 else "cinza"
    if (h < 20 or h >= 340) and v < 150:
        return "vinho" if h >= 340 or h < 8 else "marrom"
    if 20 <= h < 45 and v < 150:
        return "marrom"
    for limite, nome in ((12, "vermelha"), (40, "laranja"), (68, "amarela"), (160, "verde"), (200, "azul-clara"),
                         (255, "azul"), (290, "roxa"), (335, "rosa"), (360, "vermelha")):
        if h < limite:
            return nome
    return "colorida"


def cor_dominante(regiao, com_amostra=False):
    """Nome da cor mais presente na região (k-means com 3 grupos); com_amostra=True devolve também o BGR."""
    pix = cv2.resize(regiao, (32, 24), interpolation=cv2.INTER_AREA).reshape(-1, 3).astype(np.float32)
    crit = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 10, 1.0)
    _, rot, centros = cv2.kmeans(pix, 3, None, crit, 2, cv2.KMEANS_PP_CENTERS)
    maior = np.bincount(rot.ravel(), minlength=3).argmax()
    bgr = centros[maior].astype(np.uint8).tolist()
    return (nome_cor(bgr), bgr) if com_amostra else nome_cor(bgr)


def peso_palpite(altura_cm, razao_rosto):
    """IMC chutado pela proporção do rosto (largura/altura da caixa), vezes altura². Inventado de propósito."""
    imc = float(np.clip(22.5 + (razao_rosto - 0.80) * 35.0, 18.5, 32.0))
    return imc * (altura_cm / 100.0) ** 2


class Modelos:
    """Redes do OpenCV DNN. Use sempre na mesma thread (não são thread-safe)."""

    def __init__(self, idade=True, humor=True):
        self.idade_net = cv2.dnn.readNetFromONNX(str(MODELO_IDADE)) if idade and MODELO_IDADE.exists() else None
        self.humor_net = cv2.dnn.readNetFromONNX(str(MODELO_HUMOR)) if humor and MODELO_HUMOR.exists() else None
        self.detector = cv2.FaceDetectorYN.create(str(MODELO_ROSTO), "", (320, 240), 0.7, 0.3, 10)

    def idade_insightface(self, frame, caixa, fator=1.7, sobe=0.1, lado=96):
        """Anos (contínuo). Recorte quadrado de `fator` × o maior lado do rosto, centro um pouco acima.

        Calibrado em 11/11 com 2 fotos de domínio público: Grace Hopper (~78 anos) -> 78,
        Eileen Collins (~42) -> 46. Entrada 96×96 RGB sem normalização (o modelo normaliza).
        """
        x, y, w, h = caixa
        cx, cy = x + w / 2, y + h / 2 - sobe * h
        esc = lado / (max(w, h) * fator)
        M = np.array([[esc, 0, lado / 2 - cx * esc], [0, esc, lado / 2 - cy * esc]], np.float32)
        a = cv2.warpAffine(frame, M, (lado, lado), borderValue=0)
        self.idade_net.setInput(cv2.dnn.blobFromImage(a, 1.0, (lado, lado), (0, 0, 0), swapRB=True))
        return float(np.clip(self.idade_net.forward().ravel()[2] * 100.0, 1, 99))

    def humor(self, rosto_bgr):
        """Probabilidades das 8 expressões do FER+ (entrada: cinza 64×64, valores 0–255)."""
        g = cv2.cvtColor(rosto_bgr, cv2.COLOR_BGR2GRAY)
        b = cv2.dnn.blobFromImage(g, 1.0, (64, 64))
        self.humor_net.setInput(b)
        return _softmax(self.humor_net.forward())

    def rostos(self, frames):
        """Simulador: [(frame, caixa)] de todos os rostos nos quadros dados, do maior para o menor."""
        achados = []
        for f in frames:
            if f is None:
                continue
            h, w = f.shape[:2]
            self.detector.setInputSize((w, h))
            _, faces = self.detector.detect(f)
            if faces is None:
                continue
            achados += [(f, tuple(int(v) for v in fc[:4])) for fc in faces]
        return sorted(achados, key=lambda a: -a[1][2])

    def maior_rosto(self, frames):
        r = self.rostos(frames)
        return r[0] if r else None


class Analisador:
    """Thread que, a cada `intervalo_s`, analisa o rosto de cada dono e guarda os palpites (só na memória)."""

    def __init__(self, cfg, tracker=None, cameras=()):
        a = cfg["app"].get("analise", {})
        self.intervalo = float(a.get("intervalo_s", 4.0))
        self.usar = {"idade": a.get("idade", True), "humor": a.get("emocao", True), "altura": a.get("altura", True),
                     "peso": a.get("peso", True), "roupa": a.get("roupa", True)}
        self.tracker, self.cameras = tracker, list(cameras)
        self._alvos = {}              # pid -> pessoa (dict do estado), atualizado pelo app a cada quadro
        self._palpites = {}           # pid -> dict
        self._lock = threading.Lock()
        self._rodando = False
        self.erro = None
        self.hz = 0.0

    # ---------- API
    def iniciar(self):
        self._rodando = True
        threading.Thread(target=self._loop, name="analise", daemon=True).start()
        return self

    def parar(self):
        self._rodando = False

    def definir_alvos(self, pessoas):
        """Pessoas que interessam agora (os donos de cada lado)."""
        with self._lock:
            self._alvos = {p["id"]: dict(p) for p in pessoas if p}

    def palpite(self, pid):
        with self._lock:
            p = self._palpites.get(pid)
            return dict(p) if p else None

    # ---------- interno
    def _rosto_de(self, pid, mods, ordem):
        if self.tracker is not None:
            r = self.tracker.rosto(pid)
            if r is not None:
                return r["frame"], r["caixa"], r["cam"]
            return None
        # simulador: o 1º dono pega o maior rosto das câmeras, o 2º o segundo maior, e assim por diante
        achados = mods.rostos([c.quadro()[0] for c in self.cameras])
        if not achados:
            return None
        f, caixa = achados[ordem % len(achados)]
        return f, caixa, "simulado: rosto achado nas câmeras"

    def _analisar(self, pid, pessoa, mods, agora, ordem=0):
        ant = self._palpites.get(pid, {})
        novo = dict(ant)
        novo["id"], novo["t"], novo["visto"] = pid, agora, agora
        if self.usar["altura"]:
            novo["altura_cm"] = float(pessoa.get("y", 160.0)) + OLHOS_AO_TOPO_CM
        achado = self._rosto_de(pid, mods, ordem)
        novo["com_rosto"] = achado is not None
        if achado is None:
            return novo
        frame, caixa, fonte = achado
        novo["fonte"] = fonte
        largo = recorte(frame, caixa, 0.35)          # para mostrar
        justo = recorte(frame, caixa, 0.08)          # para o humor
        if largo is not None:
            novo["rosto"] = cv2.resize(largo, (160, int(160 * largo.shape[0] / largo.shape[1])))
        mistura = lambda velho, v: v if velho is None else (1 - SUAVE) * np.asarray(velho) + SUAVE * np.asarray(v)
        if self.usar["idade"] and mods.idade_net is not None:
            anos = mods.idade_insightface(frame, caixa)
            novo["idade"] = anos if "idade" not in ant else (1 - SUAVE) * ant["idade"] + SUAVE * anos
        if self.usar["humor"] and mods.humor_net is not None and justo is not None:
            novo["humor_probs"] = mistura(ant.get("humor_probs"), mods.humor(justo))
            i = int(np.argmax(novo["humor_probs"]))
            novo["humor"], novo["humor_conf"] = HUMORES[i], float(novo["humor_probs"][i])
        razao = caixa[2] / max(1, caixa[3])
        if self.usar["peso"] and "altura_cm" in novo:
            kg = peso_palpite(novo["altura_cm"], razao)
            novo["peso_kg"] = kg if "peso_kg" not in ant else (1 - SUAVE) * ant["peso_kg"] + SUAVE * kg
        if self.usar["roupa"]:
            reg = regiao_roupa(frame, caixa)
            if reg is not None:
                novo["roupa"], novo["roupa_bgr"] = cor_dominante(reg, com_amostra=True)
        return novo

    def _loop(self):
        try:
            mods = Modelos(self.usar["idade"], self.usar["humor"])
        except cv2.error as e:
            self.erro = f"modelos não carregaram: {e}"
            return
        n, t_n = 0, time.time()
        while self._rodando:
            agora = time.time()
            with self._lock:
                alvos = dict(self._alvos)
            fez = False
            for ordem, (pid, pessoa) in enumerate(sorted(alvos.items(), key=lambda kv: str(kv[0]))):
                ant = self._palpites.get(pid)
                if ant is not None and agora - ant["t"] < self.intervalo:
                    with self._lock:
                        ant["visto"] = agora
                    continue
                try:
                    novo = self._analisar(pid, pessoa, mods, agora, ordem)
                except cv2.error as e:              # um quadro ruim não derruba a análise
                    self.erro = str(e)[:200]
                    continue
                with self._lock:
                    self._palpites[pid] = novo
                fez = True
                n += 1
            with self._lock:                         # esquece quem saiu
                for pid in [p for p, v in self._palpites.items() if agora - v["visto"] > ESQUECER_S]:
                    del self._palpites[pid]
            if agora - t_n >= 5:
                self.hz, n, t_n = n / (agora - t_n), 0, agora
            time.sleep(0.05 if fez else 0.2)


def ficha(p, segundos=None):
    """Linhas de texto da ficha (para a 32" e para o simulador)."""
    if not p:
        return ["analisando…"]
    out = []
    if "idade" in p:
        out.append(f"idade aparente: ~{p['idade']:.0f} anos")
    if "altura_cm" in p:
        out.append(f"altura: {p['altura_cm'] / 100:.2f} m".replace(".", ","))
    if "peso_kg" in p:
        out.append(f"peso estimado: {p['peso_kg']:.0f} kg")
    if "humor" in p:
        out.append(f"humor: {p['humor']} ({p['humor_conf'] * 100:.0f}%)")
    if "roupa" in p:
        out.append(f"roupa: {p['roupa']}")
    if segundos is not None:
        out.append(f"aqui há {segundos:.0f} s")
    if not p.get("com_rosto", True):
        out.append("(sem rosto visível agora)")
    return out
