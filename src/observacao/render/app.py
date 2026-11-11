"""App de render da montagem em ILHA (desde 11/11).

Mesa no centro, telas costas com costas: cada lado (esq, dir) é uma janela
própria para quem está na zona perto daquele lado. A TV 32" fica de pé na
frente da mesa, virada para quem chega: é um painel 2D (mural), sem tracking.
Lado sem ninguém na zona perto mostra a grade das câmeras ao vivo (parede de monitores).
Medidas e zonas vêm de config/ilha.json (tools/configurar_ilha.py); regras de
quem vê quem em estado_ilha.py.

Modos de janela:
    --preview   (padrão) uma janela com as 5 telas lado a lado + mapa visto de cima.
    --telas     uma janela sem borda cobrindo os retângulos "janela" das telas físicas.

Quem está na frente:
    mapa        (preview) clique no mapa para pôr uma pessoa, arraste para mover, botão direito
                tira, roda do mouse sobre a pessoa muda a altura. C limpa.
    tracking    padrão com câmeras reais: pessoas confirmadas pelas câmeras M1-M4 (tecla T).
    --mouse     uma pessoa que segue o mouse: x do mouse = lado e distância da mesa,
                y do mouse = posição ao longo da mesa (topo da janela = fundo), roda = altura.
    --pessoas N / teclas 0-9   N pessoas simuladas circulando pelas laterais.

Uso:
    cd src && ../.venv/Scripts/python -m observacao.render.app --fake 8 --mouse
    cd src && ../.venv/Scripts/python -m observacao.render.app --fake 8 --pessoas 4 --print ../docs/render_ilha.png
Teclas: Esc sai; 0-9 pessoas automáticas; C limpa as do mapa; T tracking; Espaço mouse/tracking;
G grade; H texto; V mapa; M marcadores ArUco.
"""
import argparse
import math
import time

import numpy as np
import pyglet
from pyglet import gl

from .. import config, ilha as ilha_mod, phrases
from ..analise import Analisador, ficha
from ..bank import Banco
from ..capture import Captura
from ..estado_ilha import EstadoIlha
from ..tracker import Tracker
from . import marcadores
from .archive import GradeAoVivo, Mural
from .projection import cantos_tela, limitar_olho, matriz_off_axis, para_gl
from .scene import Cena
from .text import Letreiro

VEL_MAX = 220.0          # cm/s: o olho nunca salta
SUAVE = 5.0
UPLOADS_POR_QUADRO = 8   # texturas enviadas à GPU por quadro, somando as duas cenas
FONTE_UI = ["Segoe UI", "Helvetica", "Arial"]   # a primeira que existir (Windows / Mac)
MAPA_FRAC = 0.30        # fração da largura da janela de preview usada pelo mapa
NOMES = {"tubo_esq": "TV de tubo (esq)", "esq_21": "LED 21\" (esq)", "tubo_dir": "TV de tubo (dir)",
         "dir_21_touch": "LED 21\" (dir)", "centro_32": "TV 32\" (frente)"}


def layout_simulador(linhas, painel, W, H, x0=0, g=None, topo=None, base=None):
    """Preview em forma de simulador, em pixels do framebuffer.

    painel = a tela 32" (coluna alta à esquerda); linhas = [[telas do lado esq], [telas do lado dir]],
    cada linha na ordem em que quem está naquele lado vê. Todas as telas da mesma linha têm a mesma
    altura (proporção real de cada uma), para ficarem legíveis.
    """
    g = g or max(12, W // 120)
    topo = topo or max(60, H // 14)
    base = base or max(70, H // 10)
    asp = lambda t: t["largura_cm"] / t["altura_cm"]
    asp_linha = max(sum(asp(t) for t in l) for l in linhas)
    asp_p = asp(painel) if painel else 0.0
    nl = len(linhas)
    rot = max(44, H // 22)                       # espaço do título e do status de cada linha
    R = min((W - x0 - 3 * g - asp_p * (nl - 1) * (g + rot)) / (asp_linha + nl * asp_p + 1e-9),
            (H - topo - base - (nl - 1) * (g + rot) - rot) / nl)
    alt_total = nl * R + (nl - 1) * (g + rot)
    y_base = base + (H - topo - base - alt_total - rot) / 2
    rects = {}
    x = x0 + g
    if painel:
        wp = asp_p * alt_total
        rects[painel["id"]] = (int(x), int(y_base), int(wp), int(alt_total))
        x += wp + 2 * g
    for i, l in enumerate(linhas):                # primeira linha em cima
        y = y_base + (nl - 1 - i) * (R + g + rot)
        xl = x
        for t in l:
            w = asp(t) * R
            rects[t["id"]] = (int(xl), int(y), int(w), int(R))
            xl += w + g
    return rects


def layout_uma(tela, W, H, margem=20):
    esc = min((W - 2 * margem) / tela["largura_cm"], (H - 2 * margem - 30) / tela["altura_cm"])
    w, h = int(tela["largura_cm"] * esc), int(tela["altura_cm"] * esc)
    return {tela["id"]: ((W - w) // 2, (H - h) // 2 + 15, w, h)}


def layout_telas(telas):
    x0 = min(t["janela"]["x"] for t in telas)
    y0 = min(t["janela"]["y"] for t in telas)
    x1 = max(t["janela"]["x"] + t["janela"]["w"] for t in telas)
    y1 = max(t["janela"]["y"] + t["janela"]["h"] for t in telas)
    H = y1 - y0
    rects = {t["id"]: (t["janela"]["x"] - x0, H - (t["janela"]["y"] - y0) - t["janela"]["h"],
                       t["janela"]["w"], t["janela"]["h"]) for t in telas}
    return (x0, y0, x1 - x0, H), rects


class PessoasFalsas:
    """N pessoas circulando pelas laterais: chegam perto da mesa, se afastam, andam ao longo dela."""

    def __init__(self, ilha):
        self.ilha = ilha
        self.lista = []
        self.prox = 1001

    def definir(self, n):
        while len(self.lista) < n:
            i = len(self.lista)
            self.lista.append((self.prox, -1 if i % 2 == 0 else 1, i * 2.3 + 0.4))
            self.prox += 1
        del self.lista[n:]

    def estado(self):
        t = time.time()
        il = self.ilha
        out = []
        for pid, s, fase in self.lista:
            d = 30 + (il.longe - 40) * (0.5 + 0.5 * math.sin(t * 0.11 + fase))      # perto <-> longe
            z = 40 * math.sin(t * 0.07 + fase * 1.7) + 60 * math.sin(t * 0.031 + fase)
            out.append({"id": pid, "x": s * (il.meia + d), "y": 155 + 15 * math.sin(fase * 3.1),
                        "z": z, "segundos": 0.0, "confirmada": True})
        return out


class App:
    def __init__(self, a):
        self.a = a
        self.cfg = config.carregar(a.perfil)
        self.ilha = self.cfg.get("ilha")
        if self.ilha is None:
            raise SystemExit(f"O perfil '{a.perfil}' não é de ilha. Use --perfil ilha (ou evento), "
                             "ou render/app_u.py para a montagem antiga em U.")
        self.telas = self.ilha.telas
        self.lados = self.ilha.lados                                        # esq, dir -> referencial local
        self.cantos_local = {lado: {t["id"]: cantos_tela(t) for t in L.telas} for lado, L in self.lados.items()}

        # câmeras, tracking, banco, estado
        self.captura = Captura(self.cfg, fake=a.fake)
        self.cams = self.captura.cameras
        self.captura.iniciar(intervalo=0.2)
        masters = self.captura.por_papel("master")
        self.tracker = Tracker(self.cfg, masters).iniciar() if masters and not a.fake else None
        self.banco = Banco(self.cfg, self.cams, pasta=(config.RAIZ / "bank_teste") if a.fake else None).iniciar()
        self.estado = EstadoIlha(self.cfg, self.banco, self.ilha)
        self.analise = Analisador(self.cfg, self.tracker, self.cams).iniciar()   # palpites (só em memória)
        self._img_rosto = {}                                                      # pid -> (array, ImageData)
        self.falsas = PessoasFalsas(self.ilha)
        if a.pessoas:
            self.falsas.definir(a.pessoas)
        self.fonte_pessoas = "falsas" if a.pessoas else ("mouse" if a.mouse else "tracking")
        self.mostrar_mapa = not a.telas
        self.manuais = []                         # pessoas postas com o mouse no mapa
        self._prox_manual = 1
        self._arrasto = None
        self.geo, self.mapa_fb = None, None
        if self.fonte_pessoas == "tracking" and self.tracker is None:
            self.fonte_pessoas = "manual"         # sem câmeras reais: começa com uma pessoa no mapa
            self._nova_manual(-(self.ilha.meia + 70), 0.0)

        # janela
        cfg_gl = pyglet.gl.Config(double_buffer=True, major_version=3, minor_version=3, sample_buffers=1, samples=4)
        if a.telas:
            (x, y, w, h), self.rects = layout_telas(self.telas)
            self.win = pyglet.window.Window(w, h, style=pyglet.window.Window.WINDOW_STYLE_BORDERLESS,
                                            config=cfg_gl, vsync=True, caption="OBSERVACAO")
            self.win.set_location(x, y)
        else:
            try:
                self.win = pyglet.window.Window(1600, 900, resizable=True, config=cfg_gl, vsync=True,
                                                caption="OBSERVACAO - pre-visualizacao (ilha)")
            except pyglet.window.NoSuchConfigException:
                self.win = pyglet.window.Window(1600, 900, resizable=True, vsync=True,
                                                caption="OBSERVACAO - pre-visualizacao (ilha)")
            self._layout()

        # uma cena 3D por lado, no referencial local daquele lado
        n = len(self.cams) or 1
        self.cenas = {lado: Cena(n, L.telas, seed=7 + i, olho_ref=tuple(L.olho_ref))
                      for i, (lado, L) in enumerate(self.lados.items())}
        self.i_upload = {lado: 0 for lado in self.lados}
        self.olhos = {}                                                     # lado -> olho local suavizado
        self.mural = Mural(self.banco, self.cams)
        self.ao_vivo = GradeAoVivo(self.cams)            # lado sem dono: parede de monitores

        # frases (desligadas por padrão em app.json)
        fr = self.cfg["app"].get("frases", {})
        self.frases_ativas = fr.get("ativas", True)
        intervalo = fr.get("intervalo_s", 8.0)
        self.sorteio = phrases.Sorteio(phrases.carregar(self.cfg), seed=11)
        self.letreiros = {t["id"]: Letreiro(intervalo, atraso=i * intervalo / len(self.telas))
                          for i, t in enumerate(self.telas)}
        self.n_letreiro = {t["id"]: 0 for t in self.telas}

        self.olho_mouse = np.array([-(self.ilha.meia + 70), 160.0, 0.0])
        self.t_mouse = time.time()
        self.mostrar_texto = not a.telas
        self.mostrar_grade = True
        self.marcadores = a.marcadores
        self.img_marcadores = {}
        self.t0 = self.t_ant = time.time()
        self.fps = 0.0
        self.quadro_estado = None
        self._mapa_img, self._mapa_t = None, 0.0
        self.hud = pyglet.text.Label("", x=12, y=30, font_name="Consolas", font_size=11, color=(120, 255, 140, 255))
        self.rotulos = {}
        self.bordas = {}
        self.ajuda = pyglet.text.Label("", font_name=FONTE_UI, font_size=13, color=(230, 230, 230, 255))
        self.win.push_handlers(self)
        pyglet.clock.schedule_interval(self.tick, 1 / 60)
        if a.print:
            pyglet.clock.schedule_once(self.salvar_e_sair, a.print_s)

    # ---------- entrada
    def on_key_press(self, k, mod):
        K = pyglet.window.key
        if k == K.ESCAPE:
            self.fechar()
        elif k == K.SPACE:
            self.fonte_pessoas = "tracking" if self.fonte_pessoas != "tracking" else "mouse"
            self.t_mouse = time.time()
        elif K._0 <= k <= K._9:
            self.falsas.definir(k - K._0)
            self.fonte_pessoas = "falsas"
        elif k == K.C:
            self.manuais.clear()
            self.fonte_pessoas = "manual"
        elif k == K.T:
            self.fonte_pessoas = "tracking"
        elif k == K.G:
            self.mostrar_grade = not self.mostrar_grade
        elif k == K.M:
            self.marcadores = not self.marcadores
        elif k == K.H:
            self.mostrar_texto = not self.mostrar_texto
        elif k == K.V:
            self.mostrar_mapa = not self.mostrar_mapa
            if not self.a.telas:
                self._layout()
        return pyglet.event.EVENT_HANDLED

    def on_mouse_motion(self, x, y, dx, dy):
        if self.fonte_pessoas != "mouse" or self._no_mapa(x, y) is not None:
            return
        W, H = self.win.get_size()
        il = self.ilha
        alcance = il.meia + il.longe
        self.olho_mouse[0] = (x / W - 0.5) * 2 * alcance
        self.olho_mouse[2] = il.zona_z[1] - (y / H) * (il.zona_z[1] - il.zona_z[0])   # topo da janela = fundo

    # mapa interativo: x, y em pontos da janela -> posição no mundo (cm) ou None fora do mapa
    def _no_mapa(self, x, y):
        if not self.mostrar_mapa or self.mapa_fb is None:
            return None
        k = self.win.get_size()[0] / self.win.get_framebuffer_size()[0]
        mx, my, mw, mh = self.mapa_fb
        u, v = x / k - mx, (my + mh) - y / k
        if not (0 <= u <= mw and 0 <= v <= mh):
            return None
        return self.geo.mundo(u, v)

    def _nova_manual(self, x, z):
        p = {"id": f"P{self._prox_manual}", "x": float(x), "y": 160.0, "z": float(z), "t0": time.time()}
        self._prox_manual += 1
        self.manuais.append(p)
        return p

    def _manual_perto(self, x, z, raio=35.0):
        if not self.manuais:
            return None
        p = min(self.manuais, key=lambda q: (q["x"] - x) ** 2 + (q["z"] - z) ** 2)
        return p if math.hypot(p["x"] - x, p["z"] - z) <= raio else None

    def on_mouse_press(self, x, y, botao, mod):
        w = self._no_mapa(x, y)
        if w is None:
            return
        if self.fonte_pessoas != "manual":
            self.fonte_pessoas = "manual"
        p = self._manual_perto(*w)
        if botao == pyglet.window.mouse.RIGHT:
            if p is not None:
                self.manuais.remove(p)
            return
        self._arrasto = p or self._nova_manual(*w)

    def on_mouse_drag(self, x, y, dx, dy, botoes, mod):
        if self._arrasto is None:
            return
        w = self._no_mapa(x, y)
        if w is not None:
            self._arrasto["x"], self._arrasto["z"] = float(w[0]), float(w[1])

    def on_mouse_release(self, x, y, botao, mod):
        self._arrasto = None

    def on_mouse_scroll(self, x, y, sx, sy):
        w = self._no_mapa(x, y)
        if w is not None:
            p = self._manual_perto(*w, raio=60.0)
            if p is not None:
                p["y"] = float(np.clip(p["y"] + sy * 3, 110, 200))
            return
        self.olho_mouse[1] = float(np.clip(self.olho_mouse[1] + sy * 5, 110, 200))

    def on_resize(self, w, h):
        if not self.a.telas:
            self._layout()

    def _layout(self):
        W, H = self.win.get_framebuffer_size()
        so = [t for t in self.telas if t["id"] == self.a.so]
        if so:
            self.rects = layout_uma(so[0], W, H)
            return
        # mapa à direita; telas: 32" numa coluna, e uma linha por lado, na ordem em que quem está ali vê
        mw = int(W * MAPA_FRAC)
        self.geo = ilha_mod.GeoMapa(self.ilha, mw)
        if self.geo.altura_px > H * 0.8:
            mw = int(mw * H * 0.8 / self.geo.altura_px)
            self.geo = ilha_mod.GeoMapa(self.ilha, mw)
        mh = self.geo.altura_px
        self.mapa_fb = (W - mw - W // 80, (H - mh) // 2, mw, mh)
        linhas = []
        for lado in ("esq", "dir"):
            l = sorted(self.lados[lado].telas, key=lambda t: t["centro_cm"][0])
            linhas.append([next(t for t in self.telas if t["id"] == tl["id"]) for tl in l])
        painel = next((t for t in self.telas if t["lado"] == "frente"), None)
        area = W - mw - W // 40 if self.mostrar_mapa else W
        self.rects = layout_simulador(linhas, painel, area, H)
        self._mapa_img = None

    def on_close(self):
        self.fechar()

    # ---------- laço
    def pessoas(self):
        if self.fonte_pessoas == "mouse":
            o = self.olho_mouse
            return [{"id": "mouse", "x": float(o[0]), "y": float(o[1]), "z": float(o[2]),
                     "segundos": time.time() - self.t_mouse, "confirmada": True}]
        if self.fonte_pessoas == "falsas":
            return self.falsas.estado()
        if self.fonte_pessoas == "manual":
            agora = time.time()
            return [{"id": p["id"], "x": p["x"], "y": p["y"], "z": p["z"], "segundos": agora - p["t0"],
                     "confirmada": True} for p in self.manuais]
        return self.tracker.estado() if self.tracker else []

    def _mover_olho(self, lado, alvo_local, dt):
        cantos = list(self.cantos_local[lado].values())
        alvo = limitar_olho(alvo_local, cantos)
        atual = self.olhos.get(lado)
        if atual is None:
            self.olhos[lado] = alvo
            return
        passo = (alvo - atual) * (1 - math.exp(-dt * SUAVE))
        n = float(np.linalg.norm(passo))
        if n > VEL_MAX * dt:
            passo *= VEL_MAX * dt / n
        self.olhos[lado] = limitar_olho(atual + passo, cantos)

    def _subir(self, lado, sessao, cota):
        """Envia `cota` texturas para a cena do lado: da sessão mostrada ou das câmeras ao vivo."""
        cena = self.cenas[lado]
        n = len(self.cams)
        t = time.time() - self.t0
        for _ in range(min(cota, n)):
            i = self.i_upload[lado] % n
            self.i_upload[lado] += 1
            f = self.banco.quadro(sessao, i, t) if sessao is not None else None
            if f is None:
                f = self.cams[i].quadro()[0]
            cena.subir_quadro(i, f)

    def tick(self, dt):
        est = self.estado.atualizar(self.pessoas())
        self.quadro_estado = est
        self.analise.definir_alvos([i["dono"] for i in est["lados"].values() if i["dono"]])
        t = time.time() - self.t0
        ativos = []
        for lado, L in self.lados.items():
            info = est["lados"][lado]
            if info["modo"] == "arquivo":
                continue
            if info["modo"] != "pessoa":                       # vazio: grade ao vivo, sem 3D
                self.olhos.pop(lado, None)                     # o próximo dono começa com o olho no lugar
                continue
            d = info["dono"]
            alvo = L.local((d["x"], d["y"], d["z"]))
            self._mover_olho(lado, alvo, dt)
            ativos.append((lado, info["alvo"]))
        if ativos:
            cota = max(2, UPLOADS_POR_QUADRO // len(ativos))
            for lado, sessao in ativos:
                self._subir(lado, sessao, cota)
                self.cenas[lado].atualizar(dt)
        self.mural.atualizar(dt)
        self.ao_vivo.atualizar(dt)
        agora = time.time()
        self.fps = 0.9 * self.fps + 0.1 / max(agora - self.t_ant, 1e-3)
        self.t_ant = agora

    # ---------- frases
    def _escolher(self, tid, estilo, pessoa):
        def f():
            self.n_letreiro[tid] += 1
            k = self.n_letreiro[tid]
            if estilo == "arquivo" and k % 3 == 0:
                return (f"{self.banco.total_pessoas()} pessoas registradas esta noite. Você é uma delas.", "sistema")
            if pessoa is not None and estilo == "legenda" and k % 2 == 0:
                desc = phrases.descritivas(pessoa, self.banco, len(self.cams))
                return desc[k // 2 % len(desc)]
            return self.sorteio.proxima()
        return f

    # ---------- desenho
    def on_draw(self):
        fw, fh = self.win.get_framebuffer_size()
        W, _ = self.win.get_size()
        k = W / fw
        gl.glViewport(0, 0, fw, fh)
        gl.glClearColor(0.03, 0.03, 0.035, 1)
        gl.glClear(gl.GL_COLOR_BUFFER_BIT)
        est = self.quadro_estado
        if est is None:
            return
        textos = []
        for t in self.telas:
            tid = t["id"]
            if tid not in self.rects:
                continue
            x, y, w, h = self.rects[tid]
            gl.glEnable(gl.GL_SCISSOR_TEST)
            gl.glViewport(x, y, w, h)
            gl.glScissor(x, y, w, h)
            gl.glClearColor(0.0, 0.0, 0.0, 1)
            gl.glClear(gl.GL_COLOR_BUFFER_BIT)
            lado = t["lado"]
            if lado == "frente":                                   # 32": painel 2D
                self.mural.desenhar(tid, w, h)
                textos.append((tid, "arquivo", None, (x, y, w, h)))
            else:
                info = est["lados"][lado]
                if info["modo"] == "vazio":
                    self.ao_vivo.desenhar(tid, w, h)
                    textos.append((tid, "tela", None, (x, y, w, h)))
                elif info["modo"] == "arquivo" or lado not in self.olhos:
                    self.mural.desenhar(tid, w, h)
                    textos.append((tid, "arquivo", None, (x, y, w, h)))
                else:
                    olho = self.olhos[lado]
                    pa, pb, pc = self.cantos_local[lado][tid]
                    self.cenas[lado].desenhar(para_gl(matriz_off_axis(pa, pb, pc, olho)),
                                              grade=self.mostrar_grade, olho=olho)
                    textos.append((tid, "legenda", info.get("dono") and {**info["dono"], **info}, (x, y, w, h)))
            gl.glDisable(gl.GL_SCISSOR_TEST)
        gl.glViewport(0, 0, fw, fh)
        gl.glEnable(gl.GL_BLEND)
        gl.glBlendFunc(gl.GL_SRC_ALPHA, gl.GL_ONE_MINUS_SRC_ALPHA)
        if self.marcadores:
            self._desenhar_marcadores(k)
        agora = time.time()
        for tid, estilo, pessoa, (x, y, w, h) in (textos if self.frases_ativas and not self.marcadores else ()):
            L = self.letreiros[tid]
            L.talvez_trocar(agora, self._escolher(tid, estilo, pessoa), estilo)
            L.desenhar(int(x * k), int(y * k), int(w * k), int(h * k), agora)
        if not self.marcadores:
            self._desenhar_fichas(k)
        if self.mostrar_mapa:
            self._desenhar_mapa(k)
        if self.mostrar_texto:
            self._desenhar_hud(k)

    def _desenhar_marcadores(self, k):
        for i, t in enumerate(self.telas):
            if t["id"] not in self.rects:
                continue
            x, y, w, h = self.rects[t["id"]]
            W, H = max(1, int(w * k)), max(1, int(h * k))
            c = self.img_marcadores.get(t["id"])
            if c is None or c[0] != (W, H):
                img = np.ascontiguousarray(np.flipud(marcadores.imagem(t, i, W, H)))
                c = self.img_marcadores[t["id"]] = ((W, H), pyglet.image.ImageData(W, H, "BGR", img.tobytes(),
                                                                                     pitch=W * 3))
            c[1].blit(int(x * k), int(y * k))

    # ---------- ficha provisória na 32" (a UI definitiva substitui isto)
    def _desenhar_fichas(self, k):
        painel = next((t for t in self.telas if t["lado"] == "frente"), None)
        if painel is None or painel["id"] not in self.rects:
            return
        est = self.quadro_estado
        donos = [(lado, est["lados"][lado]) for lado in ("esq", "dir") if est["lados"][lado]["dono"]]
        if not donos:
            return
        x, y, w, h = (v * k for v in self.rects[painel["id"]])
        alt = h / len(donos)
        for i, (lado, info) in enumerate(donos):
            d = info["dono"]
            p = self.analise.palpite(d["id"])
            y1 = y + h - i * alt                       # topo deste bloco
            fundo = self.bordas.get(f"_ficha{i}")
            if fundo is None:
                fundo = self.bordas[f"_ficha{i}"] = pyglet.shapes.Rectangle(0, 0, 1, 1, color=(0, 0, 0, 205))
            fundo.x, fundo.y, fundo.width, fundo.height = x + w * 0.04, y1 - alt + 6, w * 0.92, alt - 12
            fundo.draw()
            tam = max(9, int(w / 26))
            ty = y1 - tam * 2.2
            self._rotulo(f"_ficha_t{i}", f"LADO {'ESQUERDO' if lado == 'esq' else 'DIREITO'} · PESSOA {d['id']}",
                         x + w * 0.08, ty, tam, (255, 90, 80, 255), negrito=True)
            rosto = p.get("rosto") if p else None
            ry = ty - tam
            if rosto is not None:
                c = self._img_rosto.get(d["id"])
                if c is None or c[0] is not rosto:
                    img = np.ascontiguousarray(np.flipud(rosto))
                    c = self._img_rosto[d["id"]] = (rosto, pyglet.image.ImageData(img.shape[1], img.shape[0], "BGR",
                                                                                   img.tobytes(), pitch=img.shape[1] * 3))
                esc = min(w * 0.45 / c[1].width, alt * 0.36 / c[1].height)   # cabe no bloco, sem distorcer
                lado_px = c[1].width * esc
                c[1].blit(x + w * 0.08, ry - c[1].height * esc, width=lado_px, height=c[1].height * esc)
                ry -= c[1].height * esc + tam
            else:
                ry -= tam * 0.5
            for j, linha in enumerate(ficha(p, info["segundos"])):
                ly = ry - j * tam * 1.6
                self._rotulo(f"_ficha{i}_{j}", linha, x + w * 0.08, ly, tam, (235, 235, 235, 255))
                if linha.startswith("roupa:") and p and p.get("roupa_bgr"):   # amostra da cor lida no peito
                    b_, g_, r_ = p["roupa_bgr"]
                    q = self.bordas.get(f"_amostra{i}")
                    if q is None:
                        q = self.bordas[f"_amostra{i}"] = pyglet.shapes.Rectangle(0, 0, 1, 1, color=(0, 0, 0, 255))
                    q.x, q.y, q.width, q.height = x + w * 0.08 + tam * 9.5, ly - tam * 0.15, tam * 1.2, tam * 1.2
                    q.color = (int(r_), int(g_), int(b_), 255)
                    q.draw()

    def _desenhar_mapa(self, k):
        agora = time.time()
        if self.mapa_fb is None:
            return
        mx, my, mw, mh = self.mapa_fb
        if self._mapa_img is None or agora - self._mapa_t > 0.08:
            img = ilha_mod.mapa(self.ilha, self.quadro_estado["pessoas"], self.quadro_estado, largura_px=mw)
            img = np.ascontiguousarray(np.flipud(img))
            h, w = img.shape[:2]
            self._mapa_img = pyglet.image.ImageData(w, h, "BGR", img.tobytes(), pitch=w * 3)
            self._mapa_t = agora
        self._mapa_img.blit(int(mx * k), int(my * k), width=int(mw * k), height=int(mh * k))
        self._rotulo("_mapa", "MAPA VISTO DE CIMA — clique para pôr gente, arraste, botão direito tira",
                     mx * k, (my + mh) * k + 8, 12, (230, 230, 230, 255))

    def _rotulo(self, chave, texto, x, y, tam=11, cor=(170, 170, 170, 255), negrito=False):
        r = self.rotulos.get(chave)
        if r is None:
            try:                                   # pyglet 2.1 usa weight=; 2.0 usa bold=
                r = pyglet.text.Label("", font_name=FONTE_UI, font_size=tam, color=cor,
                                      weight="bold" if negrito else "normal")
            except TypeError:
                r = pyglet.text.Label("", font_name=FONTE_UI, font_size=tam, color=cor, bold=negrito)
            self.rotulos[chave] = r
        r.text, r.x, r.y = texto, int(x), int(y)
        r.draw()

    def _status_tela(self, t):
        """O que esta tela está mostrando agora, em português."""
        if t["lado"] == "frente":
            return f"mural de quem passou · {len(self.banco.todas())} registrados"
        i = self.quadro_estado["lados"][t["lado"]]
        alvo = i["alvo"].rotulo if i.get("alvo") is not None else ""
        if i["modo"] == "arquivo":
            return f"lotado ({i['perto']} na zona perto) · mural"
        if i["modo"] == "vazio":
            return "ninguém perto · câmeras ao vivo (parede de monitores)"
        fase = {"espelho": "se vê ao vivo", "outro_lado": f"vê quem está do outro lado {alvo}",
                "anterior": f"vê a pessoa anterior {alvo}"}.get(i["fase"], i["fase"])
        alerta = "  ⚠ ALERTA 5 min" if i["alerta"] else ""
        return f"dono {i['dono']['id']} ({i['segundos']:.0f}s) · {fase}{alerta}"

    def _desenhar_hud(self, k):
        est = self.quadro_estado
        vivas = sum(1 for c in self.cams if c.quadro()[1] < 30)
        # títulos das linhas (cada lado) e da coluna da 32"
        for lado, nome in (("esq", "LADO ESQUERDO"), ("dir", "LADO DIREITO")):
            ids = [t["id"] for t in self.lados[lado].telas if t["id"] in self.rects]
            if not ids:
                continue
            x = min(self.rects[i][0] for i in ids)
            y = max(self.rects[i][1] + self.rects[i][3] for i in ids)
            i = est["lados"][lado]
            self._rotulo(f"_t_{lado}", f"{nome}  —  {i['perto']} na zona perto, {i['longe']} longe", x * k, y * k + 30,
                         14, (240, 240, 240, 255), negrito=True)
        for t in self.telas:
            if t["id"] not in self.rects:
                continue
            x, y, w, h = self.rects[t["id"]]
            b = self.bordas.get(t["id"])
            if b is None and hasattr(pyglet.shapes, "Box"):     # Box existe a partir do pyglet 2.0.10
                b = self.bordas[t["id"]] = pyglet.shapes.Box(0, 0, 1, 1, thickness=2, color=(90, 90, 90, 255))
            if b is not None:
                b.x, b.y, b.width, b.height = x * k - 2, y * k - 2, w * k + 4, h * k + 4
                b.color = (255, 210, 80, 255) if (t["lado"] in est["lados"] and est["lados"][t["lado"]]["alerta"]) \
                    else (90, 90, 90, 255)
                b.draw()
            titulo = NOMES.get(t["id"], t["id"])
            if t["lado"] == "frente":
                titulo += " — quem chega vê"
                self._rotulo(t["id"] + "_s", self._status_tela(t), x * k, (y + h) * k + 10, 11)
                self._rotulo(t["id"], titulo, x * k, (y + h) * k + 30, 14, (240, 240, 240, 255), negrito=True)
            else:
                self._rotulo(t["id"], titulo, x * k, (y + h) * k + 10, 12, (220, 220, 220, 255), negrito=True)
                self._rotulo(t["id"] + "_s", self._status_tela(t), x * k, y * k - 20, 11)
        erro = f"   ERRO análise: {self.analise.erro[:60]}" if self.analise.erro else ""
        self.hud.text = (f"fonte: {self.fonte_pessoas}   {len(est['pessoas'])} pessoas   banco {len(self.banco.todas())} "
                         f"sessões   câmeras {vivas}/{len(self.cams)}   {self.fps:3.0f} fps   "
                         f"análise {self.analise.hz:.1f}/s{erro}")
        self.hud.y = 30
        self.hud.draw()
        self.ajuda.text = ("mapa: clique põe pessoa · arraste move · botão direito tira · roda = altura      "
                           "teclas: 0-9 pessoas automáticas · C limpa · T tracking · G grade · V mapa · Esc sai")
        self.ajuda.x, self.ajuda.y = 12, 8
        self.ajuda.draw()

    def salvar_e_sair(self, dt):
        self.on_draw()
        pyglet.image.get_buffer_manager().get_color_buffer().save(self.a.print)
        print(f"imagem salva em {self.a.print}")
        self.fechar()

    def fechar(self):
        pyglet.clock.unschedule(self.tick)
        if self.tracker:
            self.tracker.parar()
        self.analise.parar()
        self.banco.parar()
        self.captura.parar()
        self.win.close()
        pyglet.app.exit()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--perfil", default="ilha")
    ap.add_argument("--telas", action="store_true")
    ap.add_argument("--mouse", action="store_true")
    ap.add_argument("--fake", type=int, default=0)
    ap.add_argument("--pessoas", type=int, default=0, help="N pessoas simuladas circulando pelas laterais")
    ap.add_argument("--so", default=None, metavar="ID", help="janela com uma tela só (ex.: centro_32, tubo_esq)")
    ap.add_argument("--marcadores", action="store_true", help="abre mostrando os marcadores ArUco")
    ap.add_argument("--print", default=None, help="salva uma imagem e sai (teste)")
    ap.add_argument("--print-s", type=float, default=6.0)
    a = ap.parse_args()
    App(a)
    pyglet.app.run()


if __name__ == "__main__":
    main()
