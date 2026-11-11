"""Montagem em ILHA: mesa no centro, telas costas com costas, público nas laterais.

Tudo que é físico vem de `config/ilha.json`, em medidas que se tiram com trena
no local (cm). Este módulo transforma essas medidas no que o resto do programa
usa: posição/ângulo de cada tela, posição/ângulo das câmeras de tracking,
zonas de circulação e o "referencial local" de cada lado.

Referencial do mundo (o mesmo do tracker):
    origem no chão, no centro da mesa;
    x  para a DIREITA de quem chega pela frente (lado "dir" = x positivo);
    y  para cima;
    z  para a FRENTE, a entrada (a TV 32" fica na frente, virada para o público).

Visto de cima (frente embaixo nesta figura, como quem chega):

     parede c/ janela | cortinas    fundo: mesa quase encostada, ninguém passa
      M3 ->   |   <- M4             câmeras de tracking na ponta do fundo
     esq  [CRT|CRT]  dir
          [LED|LED]
      M1 ->   |   <- M2             câmeras de tracking na ponta da frente
            [ 32" ]                 de pé, virada para quem chega
                                    frente / entrada

Cada lado é uma "janela" própria. Para reaproveitar a matemática que já
existia (pensada para telas olhando para +z), cada lado tem um referencial
local em que as suas telas olham para +z' e quem está daquele lado fica em
z' positivo. `Lado.local()` converte uma posição do mundo para esse referencial.
"""
import copy
import json
import math
from pathlib import Path

import numpy as np

# ------------------------------------------------------------------ padrões
# Estimativas de 10/11 a partir do desenho; tudo é remedido no local (sexta).
PADRAO = {
    "_doc": ("Medidas da montagem em ilha, em cm. Edite com tools/configurar_ilha.py "
             "(pergunta uma a uma, Enter mantém). Referencial: origem no chão no centro da mesa; "
             "x para a direita de quem chega pela frente; z para a frente (entrada)."),
    "mesa": {"comprimento_cm": 120, "largura_cm": 60, "altura_cm": 75},
    "telas": [
        # lado: esq | dir | frente.  ao_longo_cm: distância do centro da tela até a
        # PONTA DA FRENTE da mesa.  recuo_cm: quanto a face da tela fica para dentro
        # da borda da mesa.  centro_altura_cm: altura do centro da IMAGEM ao chão.
        {"id": "tubo_esq", "lado": "esq", "tipo": "crt", "largura_cm": 36, "altura_cm": 27,
         "ao_longo_cm": 90, "recuo_cm": 0, "centro_altura_cm": 97, "inclinacao": 0,
         "janela": {"x": 4920, "y": 0, "w": 960, "h": 540}, "via_videowall": "A"},
        {"id": "esq_21", "lado": "esq", "tipo": "monitor", "largura_cm": 47, "altura_cm": 27,
         "ao_longo_cm": 35, "recuo_cm": 4, "centro_altura_cm": 105, "inclinacao": 0,
         "janela": {"x": 1080, "y": 0, "w": 1920, "h": 1080}},
        {"id": "tubo_dir", "lado": "dir", "tipo": "crt", "largura_cm": 36, "altura_cm": 27,
         "ao_longo_cm": 90, "recuo_cm": 0, "centro_altura_cm": 97, "inclinacao": 0,
         "janela": {"x": 5880, "y": 0, "w": 960, "h": 540}, "via_videowall": "B"},
        {"id": "dir_21_touch", "lado": "dir", "tipo": "monitor", "touch": True, "largura_cm": 47, "altura_cm": 27,
         "ao_longo_cm": 35, "recuo_cm": 4, "centro_altura_cm": 105, "inclinacao": 0,
         "janela": {"x": 3000, "y": 0, "w": 1920, "h": 1080}},
        # 32" de pé na FRENTE da mesa, virada para quem chega. distancia_cm: da
        # ponta da frente da mesa até a tela.
        {"id": "centro_32", "lado": "frente", "tipo": "led", "largura_cm": 39, "altura_cm": 70,
         "distancia_cm": 45, "lateral_cm": 0, "centro_altura_cm": 140, "inclinacao": 0,
         "janela": {"x": 0, "y": 0, "w": 1080, "h": 1920}, "rotacao": 90},
    ],
    "tracking": [
        # Todas as câmeras ficam numa fila só, na vara longitudinal, alternando
        # esquerda/direita. ponta: frente | fundo (de qual ponta da mesa se mede).
        # afastamento_cm: distância dessa ponta, para fora (+) ou para dentro (-).
        # olha: esq | dir. Se yaw ficar null, olha reto para o lado (perpendicular
        # à mesa); se pitch ficar null, inclina para a altura dos olhos no meio
        # da zona perto. O calibrate_tracking refina no local.
        {"nome": "M1", "ponta": "frente", "olha": "esq", "altura_cm": 170, "afastamento_cm": 10, "yaw": None, "pitch": None},
        {"nome": "M2", "ponta": "frente", "olha": "dir", "altura_cm": 170, "afastamento_cm": 0, "yaw": None, "pitch": None},
        {"nome": "M3", "ponta": "fundo", "olha": "esq", "altura_cm": 170, "afastamento_cm": 0, "yaw": None, "pitch": None},
        {"nome": "M4", "ponta": "fundo", "olha": "dir", "altura_cm": 170, "afastamento_cm": 10, "yaw": None, "pitch": None},
    ],
    "zonas": {
        "_doc": ("Distâncias medidas a partir da BORDA da mesa, para fora. 'perto' = onde a tela vira janela "
                 "para quem está ali (marcar com fita no chão). 'longe' = até onde as câmeras registram. "
                 "frente_cm/fundo_cm = até onde a área de circulação vai, a partir do centro da mesa."),
        "perto_cm": 120,
        "longe_cm": 300,
        "frente_cm": 340,
        "fundo_cm": 80,
    },
    "comportamento": {
        "_doc": ("Tempos em segundos. ver_se_s: primeiros segundos a pessoa se vê ao vivo. troca_s: a cada "
                 "quanto a pessoa mostrada muda. alerta_s: aviso de consumo excessivo. sair_s: quanto tempo "
                 "fora da zona perto até a sessão fechar. lotacao: com tanta gente na zona perto de um lado, "
                 "aquele lado vira mural. troca_dono_cm: outra pessoa precisa estar tanto mais perto para "
                 "tomar a tela."),
        "ver_se_s": 10,
        "troca_s": 35,
        "alerta_s": 300,
        "sair_s": 3,
        "lotacao": 4,
        "troca_dono_cm": 30,
    },
}

LADOS = ("esq", "dir")
YAW_LADO = {"esq": -90.0, "dir": 90.0, "frente": 0.0}   # para onde a face da tela olha
SINAL = {"esq": -1.0, "dir": 1.0}


# ------------------------------------------------------------------ leitura
def _mesclar(base, extra):
    for k, v in extra.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _mesclar(base[k], v)
        else:
            base[k] = v
    return base


def carregar(caminho):
    """ilha.json mesclado sobre os padrões (chave que faltar usa o padrão)."""
    dados = copy.deepcopy(PADRAO)
    p = Path(caminho)
    if p.exists():
        with open(p, encoding="utf-8") as f:
            lido = json.load(f)
        # listas (telas, tracking) substituem inteiras; dicionários se mesclam
        for k, v in lido.items():
            if isinstance(v, dict) and isinstance(dados.get(k), dict):
                _mesclar(dados[k], v)
            else:
                dados[k] = v
    return dados


def salvar(dados, caminho):
    with open(caminho, "w", encoding="utf-8") as f:
        json.dump(dados, f, ensure_ascii=False, indent=2)


# ------------------------------------------------------------------ geometria
def gira_y(p, graus):
    """Gira p em torno de +y (mesma convenção do yaw das telas e câmeras)."""
    a = math.radians(graus)
    c, s = math.cos(a), math.sin(a)
    x, y, z = p
    return np.array([c * x + s * z, y, -s * x + c * z], dtype=float)


def yaw_para(origem, alvo):
    d = np.asarray(alvo, float) - np.asarray(origem, float)
    return math.degrees(math.atan2(d[0], d[2]))


def pitch_para(origem, alvo):
    """Positivo = inclinada para baixo (convenção do tracker)."""
    d = np.asarray(alvo, float) - np.asarray(origem, float)
    return math.degrees(math.atan2(-d[1], math.hypot(d[0], d[2])))


class Ilha:
    """Medidas -> telas, câmeras, zonas e referenciais de cada lado."""

    def __init__(self, dados):
        self.dados = dados
        m = dados["mesa"]
        self.comp = float(m["comprimento_cm"])
        self.larg = float(m["largura_cm"])
        self.meia = self.larg / 2
        self.z_frente = self.comp / 2                  # ponta da frente da mesa
        self.z_fundo = -self.comp / 2
        z = dados["zonas"]
        self.perto, self.longe = float(z["perto_cm"]), float(z["longe_cm"])
        self.zona_z = (-float(z["fundo_cm"]), float(z["frente_cm"]))
        self.comportamento = dados["comportamento"]
        self.telas = [self._tela(t) for t in dados["telas"]]
        self.masters = [self._master(c) for c in dados["tracking"]]
        self.lados = {l: Lado(l, self) for l in LADOS}

    # ---------- telas -> formato de screens.json (mundo)
    def _tela(self, t):
        lado = t["lado"]
        out = {k: v for k, v in t.items() if k not in ("ao_longo_cm", "recuo_cm", "centro_altura_cm",
                                                         "inclinacao", "distancia_cm", "lateral_cm")}
        y = float(t["centro_altura_cm"])
        if lado in SINAL:
            x = SINAL[lado] * (self.meia - float(t.get("recuo_cm", 0)))
            z = self.z_frente - float(t["ao_longo_cm"])
        else:   # frente: 32" de pé, virada para quem chega
            x = float(t.get("lateral_cm", 0))
            z = self.z_frente + float(t.get("distancia_cm", 0))
        out["centro_cm"] = [x, y, z]
        out["yaw"] = YAW_LADO[lado]
        out["pitch"] = float(t.get("inclinacao", 0))
        return out

    def centro_perto(self, lado):
        """Ponto no meio da zona perto de um lado, na altura dos olhos."""
        return np.array([SINAL[lado] * (self.meia + self.perto / 2), 155.0, 0.0])

    # ---------- câmeras de tracking
    def _master(self, c):
        z = self.z_frente + c.get("afastamento_cm", 0) if c["ponta"] == "frente" else \
            self.z_fundo - c.get("afastamento_cm", 0)
        pos = np.array([0.0, float(c["altura_cm"]), z])
        alvo = self.centro_perto(c["olha"])
        yaw = c.get("yaw")
        pitch = c.get("pitch")
        alvo = np.array([alvo[0], alvo[1], pos[2]])          # reto para o lado, mesma posição ao longo da mesa
        return {"nome": c["nome"], "posicao_cm": [round(float(v), 1) for v in pos],
                "yaw": YAW_LADO[c["olha"]] if yaw is None else float(yaw),
                "pitch": round(pitch_para(pos, alvo), 1) if pitch is None else float(pitch),
                "olha": c["olha"], "ponta": c["ponta"]}

    def aplicar_em_cameras(self, cameras_json):
        """Copia posição/ângulo das masters (estimados pelas medidas) para cameras.json, em memória.

        Câmera já calibrada no local (calibrate_tracking.py grava "calibrado": true)
        mantém o que a calibração achou: ela é mais precisa que a estimativa.
        """
        por_nome = {m["nome"]: m for m in self.masters}
        for c in cameras_json.get("cameras", []):
            m = por_nome.get(c.get("nome"))
            if m:
                c["papel"] = "master"
                if not c.get("calibrado"):
                    c["posicao_cm"], c["yaw"], c["pitch"] = m["posicao_cm"], m["yaw"], m["pitch"]
        return cameras_json

    # ---------- onde a pessoa está
    def classificar(self, p):
        """(lado, distância da borda da mesa em cm, zona) para uma posição do mundo.

        zona: 'perto' | 'longe' | None (fora da área, na frente da mesa, atrás da 32").
        """
        x, _, z = p
        if not (self.zona_z[0] <= z <= self.zona_z[1]) or abs(x) <= self.meia:
            return None, None, None
        lado = "dir" if x > 0 else "esq"
        d = abs(x) - self.meia
        if d <= self.perto:
            return lado, d, "perto"
        if d <= self.longe:
            return lado, d, "longe"
        return lado, d, None

    def telas_de(self, lado):
        return [t for t in self.telas if t["lado"] == lado]


class Lado:
    """Referencial local de um lado: as telas dele olham para +z', quem está ali fica em z' > 0."""

    def __init__(self, nome, ilha):
        self.nome = nome
        self.ilha = ilha
        self.giro = -YAW_LADO[nome]
        self.telas = []
        for t in ilha.telas_de(nome):
            tl = dict(t)
            tl["centro_cm"] = [float(v) for v in self.local(t["centro_cm"])]
            tl["yaw"] = t["yaw"] + self.giro
            self.telas.append(tl)
        plano = ilha.meia
        self.olho_ref = np.array([0.0, 155.0, plano + ilha.perto * 0.6])

    def local(self, p):
        return gira_y(np.asarray(p, float), self.giro)

    def mundo(self, pl):
        return gira_y(np.asarray(pl, float), -self.giro)


# ------------------------------------------------------------------ mapa (vista de cima)
COR = {
    "fundo": (24, 22, 20), "mesa": (120, 70, 150), "tela": (235, 200, 90), "painel": (90, 200, 235),
    "perto": (60, 110, 60), "longe": (45, 60, 45), "cam": (60, 80, 230), "cone": (40, 50, 120),
    "pessoa": (200, 200, 200), "dono": (80, 240, 255), "texto": (210, 210, 210), "grade": (40, 40, 40),
}


class GeoMapa:
    """Conversão mundo (cm) <-> pixel da imagem do mapa (origem no canto de cima, fundo em cima)."""

    def __init__(self, ilha, largura_px):
        self.x_max = ilha.meia + ilha.longe + 40
        self.z0, self.z1 = ilha.zona_z[0] - 40, ilha.zona_z[1] + 40
        self.esc = largura_px / (2 * self.x_max)
        self.largura_px = largura_px
        self.altura_px = int((self.z1 - self.z0) * self.esc)

    def px(self, x, z):
        return int((x + self.x_max) * self.esc), int((z - self.z0) * self.esc)

    def mundo(self, u, v):
        """pixel (u da esquerda, v de cima) -> (x, z) em cm."""
        return u / self.esc - self.x_max, v / self.esc + self.z0


def mapa(ilha, pessoas=(), estado=None, largura_px=520, hfov=65.0, alcance_cm=450):
    """Imagem BGR (numpy) com a vista de cima: mesa, telas, câmeras, zonas e pessoas.

    pessoas: dicts com x, z (mundo). estado: retorno de EstadoIlha.atualizar (marca os donos).
    """
    import cv2

    g = GeoMapa(ilha, largura_px)
    x_max, z0, z1, esc, altura_px = g.x_max, g.z0, g.z1, g.esc, g.altura_px
    img = np.full((altura_px, largura_px, 3), COR["fundo"], np.uint8)
    px = g.px

    for gx in range(-int(x_max) // 50 * 50, int(x_max), 50):
        cv2.line(img, px(gx, z0), px(gx, z1), COR["grade"], 1)
    for gz in range(int(z0) // 50 * 50, int(z1), 50):
        cv2.line(img, px(-x_max, gz), px(x_max, gz), COR["grade"], 1)
    # zonas
    for s in (-1, 1):
        a, b = s * ilha.meia, s * (ilha.meia + ilha.longe)
        cv2.rectangle(img, px(min(a, b), ilha.zona_z[1]), px(max(a, b), ilha.zona_z[0]), COR["longe"], -1)
        b = s * (ilha.meia + ilha.perto)
        cv2.rectangle(img, px(min(a, b), ilha.zona_z[1]), px(max(a, b), ilha.zona_z[0]), COR["perto"], -1)
    # mesa
    cv2.rectangle(img, px(-ilha.meia, ilha.z_frente), px(ilha.meia, ilha.z_fundo), COR["mesa"], -1)
    # telas (traço grosso na face, tique para onde olha)
    for t in ilha.telas:
        c = np.array(t["centro_cm"], float)
        yaw = math.radians(t["yaw"])
        n = np.array([math.sin(yaw), 0, math.cos(yaw)])
        r = np.array([math.cos(yaw), 0, -math.sin(yaw)])
        a, b = c - r * t["largura_cm"] / 2, c + r * t["largura_cm"] / 2
        cor = COR["painel"] if t["lado"] == "frente" else COR["tela"]
        cv2.line(img, px(a[0], a[2]), px(b[0], b[2]), cor, max(2, int(4 * esc)))
        cv2.arrowedLine(img, px(c[0], c[2]), px(*(c + n * 18)[[0, 2]]), cor, 1, tipLength=0.4)
        cv2.putText(img, t["id"], px(*(c + n * 22 + r * -t["largura_cm"] / 2)[[0, 2]]),
                    cv2.FONT_HERSHEY_PLAIN, 0.8, COR["texto"], 1, cv2.LINE_AA)
    # câmeras de tracking e o cone de visão
    for m in ilha.masters:
        p = np.array(m["posicao_cm"], float)
        for da in (-hfov / 2, hfov / 2):
            a = math.radians(m["yaw"] + da)
            q = p + np.array([math.sin(a), 0, math.cos(a)]) * alcance_cm
            cv2.line(img, px(p[0], p[2]), px(q[0], q[2]), COR["cone"], 1, cv2.LINE_AA)
        cv2.circle(img, px(p[0], p[2]), 5, COR["cam"], -1)
        cv2.putText(img, m["nome"], (px(p[0], p[2])[0] + 6, px(p[0], p[2])[1] + 4),
                    cv2.FONT_HERSHEY_PLAIN, 0.9, COR["cam"], 1, cv2.LINE_AA)
    # pessoas
    donos = set()
    if estado:
        donos = {l["dono"]["id"] for l in estado["lados"].values() if l.get("dono")}
    for s in (-1, 1):       # fita no chão (limite da zona perto)
        xf = s * (ilha.meia + ilha.perto)
        cv2.line(img, px(xf, ilha.zona_z[0]), px(xf, ilha.zona_z[1]), (60, 200, 230), max(1, int(esc * 3)))
    fonte = max(0.8, largura_px / 600)
    r = max(6, int(largura_px / 70))
    for p in pessoas:
        dono = p.get("id") in donos
        c = px(p["x"], p["z"])
        cv2.circle(img, c, int(r * 1.4) if dono else r, COR["dono"] if dono else COR["pessoa"],
                   -1 if dono else 2, cv2.LINE_AA)
        cv2.putText(img, str(p.get("id", ""))[-3:], (c[0] + r + 3, c[1] + 4), cv2.FONT_HERSHEY_PLAIN, fonte,
                    COR["dono"] if dono else COR["pessoa"], 1, cv2.LINE_AA)
    for s in (-1, 1):       # rótulo da fita
        cv2.putText(img, "fita", px(s * (ilha.meia + ilha.perto) - (34 if s > 0 else -4) / esc, ilha.zona_z[1] - 8),
                    cv2.FONT_HERSHEY_PLAIN, fonte * 0.9, (60, 200, 230), 1, cv2.LINE_AA)
    cv2.putText(img, "FUNDO (parede)", (6, 14), cv2.FONT_HERSHEY_PLAIN, 0.9, COR["texto"], 1, cv2.LINE_AA)
    cv2.putText(img, "FRENTE / ENTRADA", (6, altura_px - 6), cv2.FONT_HERSHEY_PLAIN, 0.9, COR["texto"], 1,
                cv2.LINE_AA)
    cv2.putText(img, "esq", (6, altura_px // 2), cv2.FONT_HERSHEY_PLAIN, 1.0, COR["texto"], 1, cv2.LINE_AA)
    cv2.putText(img, "dir", (largura_px - 30, altura_px // 2), cv2.FONT_HERSHEY_PLAIN, 1.0, COR["texto"], 1,
                cv2.LINE_AA)
    return img
