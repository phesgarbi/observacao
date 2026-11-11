"""Mural do modo 4+: grade com todo mundo que passou pela obra esta noite.

Cada tela tem a sua grade (montada com OpenCV numa imagem e enviada como uma
textura só). A cada ~0,25 s uma célula troca de imagem: outra pessoa, outra
câmera. Cada célula leva carimbo com número da pessoa e hora.
Sem sessões no banco, usa as câmeras ao vivo.
"""
import ctypes
import random
import time

import cv2
import numpy as np
from pyglet import gl
from pyglet.graphics.shader import Shader, ShaderProgram

VS = """#version 330 core
in vec2 pos;
out vec2 v_uv;
void main() { gl_Position = vec4(pos, 0.0, 1.0); v_uv = vec2(pos.x * 0.5 + 0.5, 0.5 - pos.y * 0.5); }
"""
FS = """#version 330 core
in vec2 v_uv;
uniform sampler2D tex;
uniform float tempo;
out vec4 cor;
void main() {
    vec3 c = texture(tex, v_uv).rgb;
    float linha = 0.9 + 0.1 * sin(v_uv.y * 700.0 + tempo * 5.0);
    cor = vec4(c * linha, 1.0);
}
"""


class Grade:
    def __init__(self, w_px, h_px, alvo_celula=150):
        esc = 480 / max(w_px, h_px)                    # imagem do mural: lado maior = 480 px
        self.W, self.H = max(64, int(w_px * esc)), max(64, int(h_px * esc))
        self.cols = max(2, round(self.W / alvo_celula * 1.6))
        self.cw = self.W // self.cols
        self.ch = int(self.cw * 0.75)
        self.rows = max(2, self.H // self.ch)
        self.ch = self.H // self.rows
        self.img = np.zeros((self.H, self.W, 3), np.uint8)
        self.tex = gl.GLuint()
        gl.glGenTextures(1, ctypes.byref(self.tex))
        gl.glBindTexture(gl.GL_TEXTURE_2D, self.tex)
        gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_MIN_FILTER, gl.GL_LINEAR)
        gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_MAG_FILTER, gl.GL_LINEAR)
        gl.glPixelStorei(gl.GL_UNPACK_ALIGNMENT, 1)
        gl.glTexImage2D(gl.GL_TEXTURE_2D, 0, gl.GL_RGB8, self.W, self.H, 0, gl.GL_BGR, gl.GL_UNSIGNED_BYTE,
                        self.img.ctypes.data_as(ctypes.c_void_p))
        self.sujo = True

    def celula(self, i, frame, carimbo):
        r, c = divmod(i, self.cols)
        if r >= self.rows:
            return
        x, y = c * self.cw, r * self.ch
        f = cv2.resize(frame, (self.cw - 2, self.ch - 2), interpolation=cv2.INTER_AREA)
        cinza = cv2.cvtColor(cv2.cvtColor(f, cv2.COLOR_BGR2GRAY), cv2.COLOR_GRAY2BGR)
        f = cv2.addWeighted(f, 0.45, cinza, 0.55, 0)
        cv2.putText(f, carimbo, (4, self.ch - 8), cv2.FONT_HERSHEY_PLAIN, 0.8, (150, 255, 170), 1, cv2.LINE_AA)
        self.img[y + 1:y + self.ch - 1, x + 1:x + self.cw - 1] = f
        self.sujo = True

    def subir(self):
        if not self.sujo:
            return
        gl.glBindTexture(gl.GL_TEXTURE_2D, self.tex)
        gl.glPixelStorei(gl.GL_UNPACK_ALIGNMENT, 1)
        gl.glTexSubImage2D(gl.GL_TEXTURE_2D, 0, 0, 0, self.W, self.H, gl.GL_BGR, gl.GL_UNSIGNED_BYTE,
                           np.ascontiguousarray(self.img).ctypes.data_as(ctypes.c_void_p))
        self.sujo = False


class Mural:
    def __init__(self, banco, cameras, seed=3):
        self.banco, self.cameras = banco, cameras
        self.rng = random.Random(seed)
        self.grades = {}
        self.t_troca = 0.0
        self.tempo = 0.0
        self.prog = ShaderProgram(Shader(VS, "vertex"), Shader(FS, "fragment"))
        self.vao, vbo = gl.GLuint(), gl.GLuint()
        gl.glGenVertexArrays(1, ctypes.byref(self.vao))
        gl.glGenBuffers(1, ctypes.byref(vbo))
        gl.glBindVertexArray(self.vao)
        gl.glBindBuffer(gl.GL_ARRAY_BUFFER, vbo)
        q = np.array([-1, -1, 1, -1, 1, 1, -1, -1, 1, 1, -1, 1], np.float32)
        gl.glBufferData(gl.GL_ARRAY_BUFFER, q.nbytes, q.ctypes.data_as(ctypes.c_void_p), gl.GL_STATIC_DRAW)
        loc = gl.glGetAttribLocation(self.prog.id, b"pos")
        gl.glEnableVertexAttribArray(loc)
        gl.glVertexAttribPointer(loc, 2, gl.GL_FLOAT, gl.GL_FALSE, 8, ctypes.c_void_p(0))
        gl.glBindVertexArray(0)

    def _sorteio(self):
        """(frame, carimbo) de uma pessoa qualquer do banco, ou de uma câmera ao vivo."""
        ss = self.banco.todas()
        if ss:
            s = self.rng.choice(ss)
            cams = s.cameras()
            if cams:
                lista = s.lista(self.rng.choice(cams))
                if lista:
                    f = self.banco.ler(self.rng.choice(lista))
                    if f is not None:
                        return f, f"{s.rotulo} {time.strftime('%H:%M', time.localtime(s.inicio))}"
        vivas = [c for c in self.cameras if c.quadro()[0] is not None]
        if vivas:
            c = self.rng.choice(vivas)
            return c.quadro()[0], f"AO VIVO {time.strftime('%H:%M:%S')}"
        return None, ""

    def _grade(self, tid, w, h):
        g = self.grades.get(tid)
        if g is None or g.tamanho != (w, h):               # primeira vez ou janela redimensionada
            if g is not None:
                gl.glDeleteTextures(1, ctypes.byref(g.tex))
            g = self.grades[tid] = Grade(w, h)
            g.tamanho = (w, h)
            for i in range(g.cols * g.rows):               # enche de uma vez
                f, cb = self._sorteio()
                if f is not None:
                    g.celula(i, f, cb)
        return g

    def atualizar(self, dt):
        self.tempo += dt
        self.t_troca += dt
        while self.t_troca > 0.08 and self.grades:
            self.t_troca -= 0.08
            g = self.rng.choice(list(self.grades.values()))
            f, cb = self._sorteio()
            if f is not None:
                g.celula(self.rng.randrange(g.cols * g.rows), f, cb)

    def desenhar(self, tid, w_px, h_px):
        """Desenha o mural da tela tid no viewport atual (w_px x h_px)."""
        g = self._grade(tid, w_px, h_px)
        g.subir()
        gl.glDisable(gl.GL_BLEND)
        self.prog.use()
        self.prog["tex"] = 0
        self.prog["tempo"] = self.tempo
        gl.glActiveTexture(gl.GL_TEXTURE0)
        gl.glBindTexture(gl.GL_TEXTURE_2D, g.tex)
        gl.glBindVertexArray(self.vao)
        gl.glDrawArrays(gl.GL_TRIANGLES, 0, 6)
        gl.glBindVertexArray(0)
        self.prog.stop()


class GradeAoVivo(Mural):
    """Telas de um lado sem dono: parede de monitores de segurança, uma câmera por célula, ao vivo.

    Decisão de 11/11 (Gui): de longe o público se vê na grade; quando alguém cruza
    a fita, aquele lado deixa a grade e vira a janela 3D para essa pessoa.
    Câmeras de foto (rodízio) aparecem com a última foto tirada.
    """

    def __init__(self, cameras, intervalo=0.2, celula=200):
        super().__init__(None, cameras)
        self.intervalo, self.celula = intervalo, celula
        self.t_vivo = 0.0

    def _encher(self, g):
        n = len(self.cameras)
        agora = time.strftime("%H:%M:%S")
        for i in range(g.cols * g.rows):
            if not n:
                return
            c = self.cameras[i % n]
            f = c.quadro()[0]
            if f is not None:
                g.celula(i, f, f"{c.nome} {agora}")
                r, k = divmod(i, g.cols)                      # bolinha vermelha de REC no canto da célula
                if r < g.rows:
                    cv2.circle(g.img, (k * g.cw + 9, r * g.ch + 9), 4, (40, 40, 230), -1, cv2.LINE_AA)

    def _grade(self, tid, w, h):
        g = self.grades.get(tid)
        if g is None or g.tamanho != (w, h):
            if g is not None:
                gl.glDeleteTextures(1, ctypes.byref(g.tex))
            g = self.grades[tid] = Grade(w, h, alvo_celula=self.celula)
            g.tamanho = (w, h)
            self._encher(g)
        return g

    def atualizar(self, dt):
        self.tempo += dt
        self.t_vivo += dt
        if self.t_vivo >= self.intervalo:
            self.t_vivo = 0.0
            for g in self.grades.values():
                self._encher(g)
