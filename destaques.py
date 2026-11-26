"""Marca as casas do lance sugerido com janelas translúcidas do Windows."""

import ctypes
import sys
import tkinter as tk
from ctypes import wintypes

import chess


GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x00000020
WS_EX_LAYERED = 0x00080000
WS_EX_NOACTIVATE = 0x08000000
GA_ROOT = 2
HWND_TOPMOST = -1
SW_HIDE = 0
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040
WDA_EXCLUDEFROMCAPTURE = 0x00000011

ORIGEM = ("#1384BE", "#9BE4FF")
DESTINO = ("#CA8016", "#FFE29B")


def retangulo_casa(casa, orientacao, coords, grade, origem_monitor):
    """Converte uma casa de xadrez para um retângulo em pixels da área de trabalho."""
    linha, coluna = 7 - chess.square_rank(casa), chess.square_file(casa)
    if orientacao == chess.BLACK:
        linha, coluna = 7 - linha, 7 - coluna
    esquerda, topo, _, _ = coords
    monitor_x, monitor_y = origem_monitor
    x = esquerda + monitor_x + grade.origem_x + coluna * grade.tamanho
    y = topo + monitor_y + grade.origem_y + linha * grade.tamanho
    x0, y0 = round(x), round(y)
    return x0, y0, round(x + grade.tamanho) - x0, round(y + grade.tamanho) - y0


class Destaques:
    def __init__(self, pai):
        self._pai = pai
        self._win = ctypes.WinDLL("user32", use_last_error=True)
        self._win.GetAncestor.argtypes = (wintypes.HWND, wintypes.UINT)
        self._win.GetAncestor.restype = wintypes.HWND
        self._win.GetWindowLongW.argtypes = (wintypes.HWND, ctypes.c_int)
        self._win.SetWindowLongW.argtypes = (wintypes.HWND, ctypes.c_int, wintypes.LONG)
        self._win.SetWindowPos.argtypes = (
            wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
            ctypes.c_int, ctypes.c_int, wintypes.UINT,
        )
        self._win.ShowWindow.argtypes = (wintypes.HWND, ctypes.c_int)
        self._win.SetWindowDisplayAffinity.argtypes = (wintypes.HWND, wintypes.DWORD)
        self._marcas = []
        self._retangulos = None
        self._visivel = False
        self._ocultar_na_captura = sys.getwindowsversion().build < 19041

    def _criar_marca(self, cores):
        janela = tk.Toplevel(self._pai, bg=cores[0])
        janela.withdraw()
        janela.overrideredirect(True)
        canvas = tk.Canvas(janela, bg=cores[0], borderwidth=0, highlightthickness=0)
        canvas.pack(fill="both", expand=True)

        # O Tk cria a janela nativa externa ao mapeá-la pela primeira vez.
        # Mapeia a janela transparente antes de aplicar o estilo nativo.
        janela.geometry("1x1+0+0")
        janela.attributes("-alpha", 0)
        janela.deiconify()
        janela.update_idletasks()
        hwnd = self._win.GetAncestor(janela.winfo_id(), GA_ROOT)
        if not hwnd:
            janela.destroy()
            raise ctypes.WinError(ctypes.get_last_error())
        estilo = self._win.GetWindowLongW(hwnd, GWL_EXSTYLE)
        interacao = WS_EX_TRANSPARENT | WS_EX_LAYERED | WS_EX_NOACTIVATE
        self._win.SetWindowLongW(hwnd, GWL_EXSTYLE, estilo | interacao)
        if (self._win.GetWindowLongW(hwnd, GWL_EXSTYLE) & interacao) != interacao:
            janela.destroy()
            raise OSError("O Windows recusou o estilo de janela que deixa os cliques passarem.")
        self._win.ShowWindow(hwnd, SW_HIDE)
        janela.attributes("-alpha", 0.43)
        if not self._ocultar_na_captura and not self._win.SetWindowDisplayAffinity(
            hwnd, WDA_EXCLUDEFROMCAPTURE
        ):
            self._ocultar_na_captura = True
        return janela, canvas, hwnd, cores[1]

    def mostrar(self, move, orientacao, coords, grade, origem_monitor):
        retangulos = tuple(
            retangulo_casa(casa, orientacao, coords, grade, origem_monitor)
            for casa in (move.from_square, move.to_square)
        )
        if self._visivel and self._retangulos == retangulos:
            return
        if not self._marcas:
            for cores in (ORIGEM, DESTINO):
                self._marcas.append(self._criar_marca(cores))
        for indice, (marca, retangulo) in enumerate(zip(self._marcas, retangulos)):
            _, canvas, hwnd, borda = marca
            x, y, largura, altura = retangulo
            if self._retangulos != retangulos:
                espessura = max(2, round(min(largura, altura) * 0.035))
                canvas.delete("all")
                for fator in ((1.5, 3.5) if indice == 1 else (1.5,)):
                    margem = fator * espessura
                    canvas.create_rectangle(
                        margem, margem, largura - margem, altura - margem,
                        outline=borda, width=espessura,
                    )
            if not self._win.SetWindowPos(
                hwnd, HWND_TOPMOST, x, y, largura, altura, SWP_NOACTIVATE | SWP_SHOWWINDOW
            ):
                raise ctypes.WinError(ctypes.get_last_error())
        self._retangulos = retangulos
        self._visivel = True

    def ocultar(self):
        for _, _, hwnd, _ in self._marcas:
            self._win.ShowWindow(hwnd, SW_HIDE)
        self._visivel = False

    def preparar_captura(self):
        if self._visivel and self._ocultar_na_captura:
            self.ocultar()
            ctypes.windll.dwmapi.DwmFlush()
