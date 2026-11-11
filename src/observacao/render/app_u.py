"""[MONTAGEM EM U, ANTIGA — mantida só como referência; a obra agora usa a ilha: render/app.py]
App de render: as 5 telas como janelas para o mesmo espaço 3D.

Modos de janela:
    --preview   (padrão) uma janela só com as 5 telas lado a lado, em
                miniatura, cada uma com a sua perspectiva. Serve para testar
                com um monitor só.
    --telas     uma janela sem borda cobrindo os retângulos "janela" de
                screens.json, uma área por tela física (montagem real).

Quem está na frente (fonte de pessoas):
    tracking    padrão: as pessoas confirmadas pelas câmeras master.
    --mouse     uma pessoa só, que segue o mouse (x/y) e a roda (distância).
    teclas 0-5  N pessoas simuladas andando na frente das telas (0 = ninguém).

O que cada tela mostra (state.py): quem está de frente para a tela vê a
pessoa anterior (1 pessoa), a outra (2) ou a do lado no anel (3), em 3D com
legenda. Tela que ninguém olha de frente mostra frase grande. Com 4+ pessoas:
texto enorme sobre o mural de todo mundo que passou.

Câmeras:
    padrão = config/cameras.json (fixas, rodízio de fotos, notebook)
    --fake N   N câmeras simuladas (o banco vai para bank_teste/)

Uso:
    cd src && ../.venv/Scripts/python -m observacao.render.app --mouse
    cd src && ../.venv/Scripts/python -m observacao.render.app --fake 8 --print ../docs/render.png
Teclas: Esc sai; Espaço alterna mouse/tracking; 0-5 pessoas simuladas;
G grade; H texto de diagnóstico; M marcadores ArUco (medir a montagem com fotos,
ver docs/fotos_medicao.md).
"""
import argparse
import math
import time

import numpy as np
import pyglet
from pyglet import gl

from .. import config, phrases
from ..bank import Banco
from ..capture import Captura
from ..state import Estado
from ..tracker import Tracker
from . import marcadores
from .archive import Mural
from .projection import cantos_tela, limitar_olho, matriz_off_axis, para_gl
from .scene import Cena
from .text import Letreiro

ORDEM_PREVIEW = ["tubo_esq", "esq_21", "centro_32", "dir_21_touch", "tubo_dir"]
OLHO_PADRAO = np.array([0.0, 155.0, 160.0])
VEL_MAX = 220.0          # cm/s: o olho nunca salta, mesmo com o mouse rápido
SUAVE = 5.0              # quanto maior, mais rápido o olho alcança o alvo
UPLOADS_POR_QUADRO = 8   # texturas enviadas à GPU por quadro, somando todas as cenas


def layout_uma(tela, W, H, margem=20):
    """Uma tela só, o maior possível no meio da janela, na proporção física."""
    esc = min((W - 2 * margem) / tela["largura_cm"], (H - 2 * margem - 30) / tela["altura_cm"])
    w, h = int(tela["largura_cm"] * esc), int(tela["altura_cm"] * esc)
    return {tela["id"]: ((W - w) // 2, (H - h) // 2 + 15, w, h)}


def layout_preview(telas, W, H, margem=14):
    """Retângulos (x, y, w, h) em pixels, escala física comum (cm -> px)."""
    ordem = [t for i in ORDEM_PREVIEW for t in telas if t["id"] == i] + \
            [t for t in telas if t["id"] not in ORDEM_PREVIEW]
    largura_cm = sum(t["largura_cm"] for t in ordem)
    altura_cm = max(t["altura_cm"] for t in ordem)
    escala = min((W - margem * (len(ordem) + 1)) / largura_cm, (H - 90) / altura_cm)
    x = margem
    rects = {}
    for t in ordem:
        w, h = t["largura_cm"] * escala, t["altura_cm"] * escala
        rects[t["id"]] = (int(x), int(60 + (altura_cm * escala - h) / 2), int(w), int(h))
        x += w + margem
    return rects


def layout_telas(telas):
    """Retângulos das telas físicas dentro de uma janela que cobre todas."""
    x0 = min(t["janela"]["x"] for t in telas)
    y0 = min(t["janela"]["y"] for t in telas)
    x1 = max(t["janela"]["x"] + t["janela"]["w"] for t in telas)
    y1 = max(t["janela"]["y"] + t["janela"]["h"] for t in telas)
    H = y1 - y0
    rects = {t["id"]: (t["janela"]["x"] - x0, H - (t["janela"]["y"] - y0) - t["janela"]["h"],
                       t["janela"]["w"], t["janela"]["h"]) for t in telas}
    return (x0, y0, x1 - x0, H), rects


class PessoasFalsas:
    """N pessoas andando devagar na frente das telas, para testar sem gente."""

    def __init__(self):
        self.lista = []           # (id, t_entrada, fase)
        self.prox = 1001

    def definir(self, n):
        while len(self.lista) < n:
            self.lista.append((self.prox, time.time(), len(self.lista) * 2.1 + 0.7))
            self.prox += 1
        del self.lista[n:]

    def estado(self):
        t = time.time()
        n = max(1, len(self.lista))
        out = []
        for i, (pid, t0, fase) in enumerate(self.lista):
            base = -70 + 140 * (i + 0.5) / n
            out.append({"id": pid, "x": base + 25 * math.sin(t * 0.3 + fase),
                        "y": 150 + 12 * math.sin(t * 0.21 + fase * 2),
                        "z": 150 + 40 * math.sin(t * 0.17 + fase), "segundos": t - t0, "confirmada": True})
        return out


class App:
    def __init__(self, a):
        self.a = a
        self.cfg = config.carregar(a.perfil)
        self.telas = self.cfg["telas"]["telas"]
        self.cantos = {t["id"]: cantos_tela(t) for t in self.telas}
        self.lista_cantos = list(self.cantos.values())

        # câmeras, tracking, banco, estado
        self.captura = Captura(self.cfg, fake=a.fake)
        self.cams = self.captura.cameras
        self.captura.iniciar(intervalo=0.2)
        masters = self.captura.por_papel("master")
        self.tracker = Tracker(self.cfg, masters).iniciar() if masters and not a.fake else None
        self.banco = Banco(self.cfg, self.cams, pasta=(config.RAIZ / "bank_teste") if a.fake else None).iniciar()
        self.estado = Estado(self.cfg, self.banco, self.telas)
        self.falsas = PessoasFalsas()
        self.fonte_pessoas = "mouse" if a.mouse else "tracking"
        self.t_mouse = time.time()

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
                                                caption="OBSERVACAO - pre-visualizacao")
            except pyglet.window.NoSuchConfigException:
                self.win = pyglet.window.Window(1600, 900, resizable=True, vsync=True,
                                                caption="OBSERVACAO - pre-visualizacao")
            self._layout()

        # uma cena 3D por pessoa (até 3); a cena 0 também serve ao modo vazio
        n = len(self.cams) or 1
        self.cenas = [Cena(n, self.telas, seed=7 + i) for i in range(3)]
        self.i_upload = [0, 0, 0]
        self.olhos = {}                                 # slot -> olho suavizado
        self.mural = Mural(self.banco, self.cams)

        # frases
        fr = self.cfg["app"].get("frases", {})
        self.frases_ativas = fr.get("ativas", True)       # desligadas por enquanto: texto vai virar espacial
        intervalo = fr.get("intervalo_s", 8.0)
        self.sorteio = phrases.Sorteio(phrases.carregar(self.cfg), seed=11)
        self.letreiros = {t["id"]: Letreiro(intervalo, atraso=i * intervalo / len(self.telas))
                          for i, t in enumerate(self.telas)}
        self.n_letreiro = {t["id"]: 0 for t in self.telas}

        self.olho_mouse = OLHO_PADRAO.copy()
        self.mostrar_texto = not a.telas
        self.mostrar_grade = True
        self.marcadores = a.marcadores
        self.img_marcadores = {}                        # tid -> (tamanho, ImageData)
        self.t0 = time.time()
        self.t_ant = time.time()
        self.fps = 0.0
        self.quadro_estado = {"modo": "vazio", "pessoas": [], "telas": {}}
        self.hud = pyglet.text.Label("", x=12, y=12, font_name="Consolas", font_size=11, color=(120, 255, 140, 255))
        self.rotulos = {}
        self.win.push_handlers(self)
        pyglet.clock.schedule_interval(self.tick, 1 / 60)
        if a.print:
            pyglet.clock.schedule_once(self.salvar_e_sair, a.print_s)

    # ---------- entrada ----------
    def on_key_press(self, k, mod):
        K = pyglet.window.key
        if k == K.ESCAPE:
            self.fechar()
        elif k == K.SPACE:
            self.fonte_pessoas = "tracking" if self.fonte_pessoas != "tracking" else "mouse"
            self.t_mouse = time.time()
        elif K._0 <= k <= K._5:
            self.falsas.definir(k - K._0)
            self.fonte_pessoas = "falsas"
        elif k == K.G:
            self.mostrar_grade = not self.mostrar_grade
        elif k == K.M:
            self.marcadores = not self.marcadores
        elif k == K.H:
            self.mostrar_texto = not self.mostrar_texto
        return pyglet.event.EVENT_HANDLED

    def on_mouse_motion(self, x, y, dx, dy):
        W, H = self.win.get_size()
        self.olho_mouse[0] = (x / W - 0.5) * 220            # -110..110 cm (dentro do U)
        self.olho_mouse[1] = 110 + (y / H) * 90             # 110..200 cm (alturas reais de olhos)

    def on_mouse_scroll(self, x, y, sx, sy):
        self.olho_mouse[2] = float(np.clip(self.olho_mouse[2] - sy * 10, 70, 320))

    def on_resize(self, w, h):
        if not self.a.telas:
            self._layout()

    def _layout(self):
        W, H = self.win.get_framebuffer_size()
        so = [t for t in self.telas if t["id"] == self.a.so]
        self.rects = layout_uma(so[0], W, H) if so else layout_preview(self.telas, W, H)

    def on_close(self):
        self.fechar()

    # ---------- laço ----------
    def pessoas(self):
        if self.fonte_pessoas == "mouse":
            o = self.olho_mouse
            return [{"id": "mouse", "x": float(o[0]), "y": float(o[1]), "z": float(o[2]),
                     "segundos": time.time() - self.t_mouse, "confirmada": True}]
        if self.fonte_pessoas == "falsas":
            return self.falsas.estado()
        return self.tracker.estado()[:5] if self.tracker else []

    def _mover_olho(self, slot, alvo, dt):
        alvo = limitar_olho(alvo, self.lista_cantos)
        atual = self.olhos.get(slot)
        if atual is None:
            self.olhos[slot] = alvo
            return alvo
        passo = (alvo - atual) * (1 - math.exp(-dt * SUAVE))
        n = float(np.linalg.norm(passo))
        if n > VEL_MAX * dt:
            passo *= VEL_MAX * dt / n
        self.olhos[slot] = limitar_olho(atual + passo, self.lista_cantos)
        return self.olhos[slot]

    def _subir(self, slot, sessao, cota):
        """Envia `cota` texturas para a cena do slot: da sessão mostrada ou das câmeras ao vivo."""
        cena = self.cenas[slot]
        n = len(self.cams)
        t = time.time() - self.t0
        for _ in range(min(cota, n)):
            i = self.i_upload[slot] % n
            self.i_upload[slot] += 1
            f = self.banco.quadro(sessao, i, t) if sessao is not None else None
            if f is None:
                f = self.cams[i].quadro()[0]
            cena.subir_quadro(i, f)

    def tick(self, dt):
        est = self.estado.atualizar(self.pessoas())
        self.quadro_estado = est
        ativos = []                                   # (slot, sessão mostrada)
        if est["modo"] == "vazio":
            t = time.time() - self.t0
            deriva = OLHO_PADRAO + np.array([55 * math.sin(t * 0.21), 18 * math.sin(t * 0.13), 30 * math.sin(t * 0.17)])
            self._mover_olho("vazio", deriva, dt)
            ativos.append((0, self.banco.anterior()))
        elif est["modo"] == "pessoas":
            for p in est["pessoas"]:
                self._mover_olho(p["slot"], np.array([p["x"], p["y"], p["z"]]), dt)
                ativos.append((p["slot"], p["alvo"]))
        if ativos:
            cota = max(2, UPLOADS_POR_QUADRO // len(ativos))
            for slot, sessao in ativos:
                self._subir(slot, sessao, cota)
                self.cenas[slot].atualizar(dt)
        else:
            self.mural.atualizar(dt)
        agora = time.time()
        self.fps = 0.9 * self.fps + 0.1 / max(agora - self.t_ant, 1e-3)
        self.t_ant = agora

    # ---------- frases ----------
    def _escolher(self, tid, estilo, pessoa):
        """Função que devolve a próxima frase para o letreiro desta tela."""
        def f():
            self.n_letreiro[tid] += 1
            k = self.n_letreiro[tid]
            if estilo == "arquivo" and k % 3 == 0:
                return (f"{self.banco.total_pessoas()} pessoas registradas esta noite. Você é uma delas.", "sistema")
            if pessoa is not None and (estilo == "legenda" and k % 2 == 0 or estilo == "tela" and k % 3 == 0):
                desc = phrases.descritivas(pessoa, self.banco, len(self.cams))
                return desc[k // 2 % len(desc)]
            return self.sorteio.proxima()
        return f

    # ---------- desenho ----------
    def on_draw(self):
        fw, fh = self.win.get_framebuffer_size()
        W, _ = self.win.get_size()
        k = W / fw
        gl.glViewport(0, 0, fw, fh)
        gl.glClearColor(0.03, 0.03, 0.035, 1)
        gl.glClear(gl.GL_COLOR_BUFFER_BIT)
        est = self.quadro_estado
        por_id = {p["id"]: p for p in est["pessoas"]}
        agora = time.time()
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
            dono = est["telas"].get(tid)
            pa, pb, pc = self.cantos[tid]
            if est["modo"] == "arquivo":
                self.mural.desenhar(tid, w, h)
                estilo, pessoa = "arquivo", None
            elif dono == "vazio" and "vazio" in self.olhos:
                self.cenas[0].desenhar(para_gl(matriz_off_axis(pa, pb, pc, self.olhos["vazio"])),
                                       grade=self.mostrar_grade, olho=self.olhos["vazio"])
                estilo, pessoa = "legenda", None
            elif dono in por_id and por_id[dono]["slot"] in self.olhos:
                p = por_id[dono]
                olho = self.olhos[p["slot"]]
                self.cenas[p["slot"]].desenhar(para_gl(matriz_off_axis(pa, pb, pc, olho)),
                                               grade=self.mostrar_grade, olho=olho)
                estilo, pessoa = "legenda", p
            else:
                perto = min(est["pessoas"], key=lambda q: q["z"]) if est["pessoas"] else None
                estilo, pessoa = "tela", perto
                if not self.frases_ativas:                 # sem texto: a tela vira janela para quem está mais perto
                    slot = perto["slot"] if perto is not None and perto["slot"] in self.olhos else None
                    if slot is None and "vazio" in self.olhos:
                        slot, olho = 0, self.olhos["vazio"]
                    elif slot is not None:
                        olho = self.olhos[slot]
                    if slot is not None:
                        self.cenas[slot].desenhar(para_gl(matriz_off_axis(pa, pb, pc, olho)),
                                                  grade=self.mostrar_grade, olho=olho)
            gl.glDisable(gl.GL_SCISSOR_TEST)
            textos.append((tid, estilo, pessoa, (x, y, w, h)))
        gl.glViewport(0, 0, fw, fh)
        gl.glEnable(gl.GL_BLEND)
        gl.glBlendFunc(gl.GL_SRC_ALPHA, gl.GL_ONE_MINUS_SRC_ALPHA)
        if self.marcadores:
            self._desenhar_marcadores(k)
        for tid, estilo, pessoa, (x, y, w, h) in (textos if self.frases_ativas and not self.marcadores else ()):
            L = self.letreiros[tid]
            L.talvez_trocar(agora, self._escolher(tid, estilo, pessoa), estilo)
            L.desenhar(int(x * k), int(y * k), int(w * k), int(h * k), agora)
        if self.mostrar_texto:
            self._desenhar_hud(k)

    def _desenhar_marcadores(self, k):
        """Grade ArUco em cada tela, por cima de tudo (tools/fotos_telas.py lê as fotos)."""
        for i, t in enumerate(self.telas):
            if t["id"] not in self.rects:
                continue
            x, y, w, h = self.rects[t["id"]]
            W, H = max(1, int(w * k)), max(1, int(h * k))
            c = self.img_marcadores.get(t["id"])
            if c is None or c[0] != (W, H):
                img = np.ascontiguousarray(np.flipud(marcadores.imagem(t, i, W, H)))
                c = self.img_marcadores[t["id"]] = ((W, H), pyglet.image.ImageData(W, H, "BGR", img.tobytes(), pitch=W * 3))
            c[1].blit(int(x * k), int(y * k))

    def _desenhar_hud(self, k):
        est = self.quadro_estado
        vivas = sum(1 for c in self.cams if c.quadro()[1] < 30)
        quem = "  ".join(f"[{p['id']} vê {p['alvo'].rotulo if p['alvo'] else 'ao vivo'}]" for p in est["pessoas"])
        self.hud.text = (f"modo {est['modo']} ({self.estado.quantas} pessoas, fonte {self.fonte_pessoas})  {quem}   "
                         f"banco {len(self.banco.todas())} sessões   cameras {vivas}/{len(self.cams)}   {self.fps:3.0f} fps   "
                         f"[espaco] mouse/tracking [0-5] simuladas [G] grade [M] marcadores [H] texto [Esc] sai")
        self.hud.draw()
        for t in self.telas:
            if t["id"] not in self.rects:
                continue
            x, y, w, h = self.rects[t["id"]]
            dono = self.quadro_estado["telas"].get(t["id"])
            r = self.rotulos.get(t["id"])
            if r is None:
                r = self.rotulos[t["id"]] = pyglet.text.Label("", font_name="Consolas", font_size=10,
                                                              color=(150, 150, 150, 255))
            r.text = f"{t['id']}  ({'texto' if dono is None else dono})"
            r.x, r.y = int(x * k), int((y + h) * k + 6)
            r.draw()

    def salvar_e_sair(self, dt):
        self.on_draw()
        pyglet.image.get_buffer_manager().get_color_buffer().save(self.a.print)
        print(f"imagem salva em {self.a.print}")
        self.fechar()

    def fechar(self):
        pyglet.clock.unschedule(self.tick)
        if self.tracker:
            self.tracker.parar()
        self.banco.parar()
        self.captura.parar()
        self.win.close()
        pyglet.app.exit()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--perfil", default="casa")   # perfis do U (casa, casa_amigo)
    ap.add_argument("--telas", action="store_true")
    ap.add_argument("--mouse", action="store_true")
    ap.add_argument("--fake", type=int, default=0)
    ap.add_argument("--so", default=None, metavar="ID", help="janela com uma tela só (ex.: centro_32, tubo_esq)")
    ap.add_argument("--marcadores", action="store_true", help="abre mostrando os marcadores ArUco")
    ap.add_argument("--print", default=None, help="salva uma imagem e sai (teste)")
    ap.add_argument("--print-s", type=float, default=6.0)
    a = ap.parse_args()
    App(a)
    pyglet.app.run()


if __name__ == "__main__":
    main()
