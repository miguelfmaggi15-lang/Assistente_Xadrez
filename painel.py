"""Painel de acompanhamento da partida."""

import tkinter as tk

from destaques import DESTINO, ORIGEM


FUNDO = "#111B25"
LINHA = "#2C3D4D"
TEXTO = "#F4F7FA"
SECUNDARIO = "#AABCCB"


class Painel(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Assistente de Xadrez")
        self.attributes("-topmost", True)
        self.overrideredirect(True)
        self.configure(bg=FUNDO, highlightbackground=LINHA, highlightthickness=1)
        escala = self.winfo_fpixels("1i") / 96
        self.largura = round(324 * escala)
        self.altura = round(166 * escala)
        margem = round(16 * escala)
        self.geometry(f"{self.largura}x{self.altura}+20+20")

        cabecalho = tk.Frame(self, bg=FUNDO)
        cabecalho.pack(fill="x", padx=margem, pady=(round(10 * escala), round(8 * escala)))
        self._texto(cabecalho, "Assistente de Xadrez", 11, TEXTO, "bold").pack(side="left")
        self._estado = self._texto(cabecalho, "", 9, SECUNDARIO, "bold")
        self._estado.pack(side="right")

        tk.Frame(self, height=1, bg=LINHA).pack(fill="x", padx=margem)
        corpo = tk.Frame(self, bg=FUNDO)
        corpo.pack(fill="both", expand=True, padx=margem, pady=round(5 * escala))
        corpo.grid_columnconfigure(0, weight=1)
        corpo.grid_rowconfigure(0, weight=1)

        self._trajeto = tk.Frame(corpo, bg=FUNDO)
        self._trajeto.grid(row=0, column=0, sticky="ew")
        self._trajeto.grid_columnconfigure((0, 2), weight=1, uniform="casa")
        self._origem = self._casa(self._trajeto, 0, "Origem", ORIGEM[1])
        self._texto(self._trajeto, "→", 24, SECUNDARIO).grid(row=0, column=1, rowspan=2)
        self._destino = self._casa(self._trajeto, 2, "Destino", DESTINO[1])
        self._mensagem = self._texto(corpo, "", 15, TEXTO, "bold")
        self._mensagem.configure(wraplength=self.largura - 2 * margem, justify="left")
        self._mensagem.grid(row=0, column=0, sticky="w")

        tk.Frame(self, height=1, bg=LINHA).pack(fill="x", padx=margem)
        rodape = tk.Frame(self, bg=FUNDO)
        rodape.pack(fill="x", padx=margem, pady=(round(7 * escala), round(9 * escala)))
        self._texto(rodape, "Ctrl+C", 9, SECUNDARIO).pack(side="right", padx=(8, 0))
        self._detalhe = self._texto(rodape, "", 9, SECUNDARIO)
        self._detalhe.configure(wraplength=self.largura - 2 * margem - round(55 * escala))
        self._detalhe.pack(side="left", fill="x", expand=True)
        self._conteudo = None
        self.espera("Aguardando tabuleiro", "Abra o tabuleiro no Chrome")

    @staticmethod
    def _texto(pai, texto, tamanho, cor, peso="normal", fonte="Segoe UI"):
        return tk.Label(
            pai, text=texto, fg=cor, bg=FUNDO, font=(fonte, tamanho, peso),
            anchor="w", padx=0, pady=0, borderwidth=0,
        )

    def _casa(self, pai, coluna, legenda, cor):
        self._texto(pai, legenda, 9, SECUNDARIO).grid(row=0, column=coluna, sticky="s")
        casa = self._texto(pai, "", 27, cor, "bold", "Consolas")
        casa.grid(row=1, column=coluna, sticky="n")
        return casa

    def espera(self, mensagem, detalhe="", estado="Em espera"):
        conteudo = (mensagem, detalhe, estado)
        if self._conteudo == conteudo:
            return
        self._conteudo = conteudo
        self._estado.configure(text=estado, fg=SECUNDARIO)
        self._trajeto.grid_remove()
        self._mensagem.configure(text=mensagem)
        self._mensagem.grid()
        self._detalhe.configure(text=detalhe)

    def jogada(self, origem, destino, detalhe):
        conteudo = (origem, destino, detalhe, "Sua vez")
        if self._conteudo == conteudo:
            return
        self._conteudo = conteudo
        self._estado.configure(text="Sua vez", fg=ORIGEM[1])
        self._mensagem.grid_remove()
        self._origem.configure(text=origem)
        self._destino.configure(text=destino)
        self._trajeto.grid()
        self._detalhe.configure(text=detalhe)
