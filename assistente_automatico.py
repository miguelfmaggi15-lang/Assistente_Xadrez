"""
ASSISTENTE DE XADREZ - 100% AUTOMÁTICO
========================================
1. Foca o Chrome já aberto
2. Detecta o tabuleiro na tela automaticamente
3. Reconhece as peças por template matching invariante ao fundo
4. Identifica o turno pelo destaque do último lance e acompanha lances legais
5. Calcula a melhor jogada com Stockfish (helper Node.js)
6. Mostra a jogada em um overlay sempre no topo
7. Repete até a partida acabar

Uso:
    python assistente_automatico.py

Requisitos:
    - Chrome aberto com chess.com e partida em andamento
    - Tema padrão do chess.com
    - Imagens de referência na pasta 'referencias/'
"""

import argparse
import io
import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
from pathlib import Path

import chess
import cv2
import mss
import numpy as np
import pygetwindow as gw

import deteccao
from destaques import Destaques
from painel import Painel

if isinstance(sys.stdout, io.TextIOWrapper):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

INTERVALO_LEITURA = 0.30
INTERVALO_CONFIRMACAO_FIM = 1.0
PROFUNDIDADE_STOCKFISH = 15
TEMPO_STOCKFISH_MS = 500
TIMEOUT_STOCKFISH = 8.0
LIMIAR_TEMPLATE = 0.40
CONFIRMACOES_FIM = 5
MIN_PECAS_LEITURA = 2
TEMPO_RESINCRONIZACAO = 6.0
MODO_DEBUG = False
PASTA_PROJETO = Path(__file__).resolve().parent
NOME_HELPER = PASTA_PROJETO / "stockfish_helper.js"
ARQUIVO_LOG = PASTA_PROJETO / "debug_sessao.log"


def _abrir_mss():
    return mss.MSS() if hasattr(mss, "MSS") else mss.mss()


class AssistenteXadrez:
    def __init__(self, orientacao=None, tempo_stockfish_ms=TEMPO_STOCKFISH_MS):
        print("Assistente de Xadrez")
        print("Abra uma partida contra um bot no Chrome. Ctrl+C encerra o assistente.\n")
        print("Stockfish  | Iniciando motor")
        self.tempo_stockfish_ms = tempo_stockfish_ms
        self.engine_proc = None
        self._fila_respostas = queue.Queue()
        self._req_id = 0
        self._ultimo_fen_enviado = None
        self._jogada_cache_uci = None
        self._jogada_cache_desc = None
        self._iniciar_engine()

        print("Leitura    | Carregando referências das peças")
        self.referencias = deteccao.carregar_referencias(PASTA_PROJETO / "referencias")

        self._orientacao_manual = orientacao
        self.cor_jogador = None
        self.board = None
        self.pecas_estaveis = None
        self._pecas_pendentes = None
        self.jogada_anterior = None
        self.contador_partida_encerrada = 0
        self.partida_ativa = True

        self.coords_tabuleiro = None
        self.monitor_indice = 1
        self._falhas_tabuleiro = 0
        self._mensagem_status = None
        self._grade = None
        self._grade_coords = None
        self._ultimo_diag = None
        self._ultimo_diag_em = 0.0
        self._poucas_pecas = 0
        self._instavel_desde = None

        self.overlay = None
        self.destaques = None
        self._origem_monitor = (0, 0)
        self._criar_overlay()

    # -----------------------------------------------------------------------
    # STOCKFISH (helper Node.js)
    # -----------------------------------------------------------------------
    def _iniciar_engine(self, anunciar=True):
        self._encerrar_engine()
        try:
            ambiente = os.environ.copy()
            ambiente["SF_DEPTH"] = str(PROFUNDIDADE_STOCKFISH)
            ambiente["SF_MOVETIME"] = str(self.tempo_stockfish_ms)
            self.engine_proc = subprocess.Popen(
                ["node", str(NOME_HELPER)],
                env=ambiente,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                bufsize=1,
            )
            fila = queue.Queue()
            self._fila_respostas = fila
            threading.Thread(
                target=self._ler_engine, args=(self.engine_proc, fila), daemon=True
            ).start()
            self._ultimo_fen_enviado = None
            self._jogada_cache_uci = None
            self._jogada_cache_desc = None
            if anunciar:
                print("Stockfish  | Processo iniciado")
            return True
        except Exception as erro:
            print(f"Stockfish  | Erro ao iniciar: {erro}")
            self.engine_proc = None
            return False

    def _encerrar_engine(self):
        processo = self.engine_proc
        self.engine_proc = None
        if processo is None:
            return
        try:
            if processo.stdin is not None:
                processo.stdin.close()
        except Exception:
            pass
        try:
            processo.terminate()
            processo.wait(timeout=3)
        except Exception:
            try:
                processo.kill()
            except Exception:
                pass

    @staticmethod
    def _ler_engine(processo, fila):
        try:
            if processo.stdout is None:
                return
            for linha in processo.stdout:
                linha = linha.strip()
                if not linha:
                    continue
                partes = linha.split(" ", 1)
                if len(partes) == 2:
                    fila.put((partes[0], partes[1]))
                else:
                    fila.put(("", partes[0]))
        except Exception:
            pass

    def calcular_melhor_jogada(self, board):
        if self.engine_proc is None or self.engine_proc.poll() is not None:
            if not self._iniciar_engine(anunciar=False):
                return None, None

        fen = board.fen()
        if fen == self._ultimo_fen_enviado and self._jogada_cache_uci:
            return self._jogada_cache_uci, self._jogada_cache_desc

        processo = self.engine_proc
        if processo is None or processo.stdin is None:
            return None, None

        self._req_id += 1
        requisicao = str(self._req_id)

        try:
            processo.stdin.write(f"{requisicao}|{fen}\n")
            processo.stdin.flush()
        except Exception as erro:
            print(f"Stockfish  | Falha ao enviar posição: {erro}")
            self._iniciar_engine(anunciar=False)
            return None, None

        timeout = max(TIMEOUT_STOCKFISH, self.tempo_stockfish_ms / 1000 + 2)
        prazo = time.monotonic() + timeout
        jogada = None
        while True:
            restante = prazo - time.monotonic()
            if restante <= 0:
                break
            try:
                resposta_id, resposta = self._fila_respostas.get(timeout=restante)
            except queue.Empty:
                break
            if resposta_id != requisicao:
                continue
            jogada = resposta.strip()
            break

        if jogada is None:
            print("Stockfish  | Tempo esgotado. Reiniciando motor.")
            self._iniciar_engine(anunciar=False)
            return None, None

        try:
            board.parse_uci(jogada)
        except ValueError:
            return None, None
        if jogada == "0000":
            return None, None

        descricao = self._uci_para_humano(jogada, board)
        self._ultimo_fen_enviado = fen
        self._jogada_cache_uci = jogada
        self._jogada_cache_desc = descricao
        return jogada, descricao

    def _uci_para_humano(self, uci, board):
        if len(uci) < 4:
            return uci
        origem = uci[:2]
        destino = uci[2:4]
        try:
            peca = board.piece_at(chess.parse_square(origem))
            if peca is not None:
                nomes = {
                    chess.PAWN: "Peão",
                    chess.ROOK: "Torre",
                    chess.KNIGHT: "Cavalo",
                    chess.BISHOP: "Bispo",
                    chess.QUEEN: "Dama",
                    chess.KING: "Rei",
                }
                nome = nomes.get(peca.piece_type, "Peça")
                cor = "Branca" if peca.color else "Preta"
                texto = f"{nome} {origem} → {destino} ({cor})"
                if len(uci) >= 5:
                    texto += f" ={deteccao.SIMBOLOS.get(uci[4].upper(), uci[4])}"
                return texto
        except Exception:
            pass
        return f"{origem} → {destino}"

    # -----------------------------------------------------------------------
    # OVERLAY
    # -----------------------------------------------------------------------
    def _criar_overlay(self):
        try:
            # A captura e as janelas precisam usar os mesmos pixels físicos.
            with _abrir_mss():
                pass
            self.overlay = Painel()
            self.overlay.update_idletasks()
            self.overlay.update()
        except Exception as erro:
            print(f"Painel     | Falha ao abrir: {erro}")
            self.overlay = None
            return

        try:
            self.destaques = Destaques(self.overlay)
        except OSError as erro:
            print(f"Marcações  | Falha ao preparar: {erro}")

    def _posicionar_overlay(self):
        if self.overlay is None:
            return
        try:
            with _abrir_mss() as sct:
                if not (0 < self.monitor_indice < len(sct.monitors)):
                    return
                monitor = sct.monitors[self.monitor_indice]
            largura, altura = self.overlay.largura, self.overlay.altura
            esquerda, topo = monitor["left"], monitor["top"]
            direita, baixo = esquerda + monitor["width"], topo + monitor["height"]
            x, y = direita - largura - 20, topo + 20
            if self.coords_tabuleiro is not None:
                bx, by, bw, bh = self.coords_tabuleiro
                bx, by = esquerda + bx, topo + by
                cantos = (
                    (x, y), (esquerda + 20, y),
                    (x, baixo - altura - 20), (esquerda + 20, baixo - altura - 20),
                )
                x, y = min(cantos, key=lambda canto: (
                    max(0, min(canto[0] + largura, bx + bw) - max(canto[0], bx))
                    * max(0, min(canto[1] + altura, by + bh) - max(canto[1], by))
                ))
            self.overlay.geometry(f"{largura}x{altura}+{x}+{y}")
            self.overlay.update()
        except Exception:
            pass

    def _exibir_espera(self, mensagem, detalhe="", estado="Em espera"):
        if self.destaques is not None:
            self.destaques.ocultar()
        if self.overlay is not None:
            self.overlay.espera(mensagem, detalhe, estado)

    def _exibir_jogada(self, board, uci):
        if self.overlay is not None:
            peca = board.piece_at(chess.parse_square(uci[:2]))
            detalhe = deteccao.SIMBOLOS[peca.symbol()]
            if len(uci) > 4:
                detalhe = f"Promoção a {deteccao.SIMBOLOS[uci[4]].lower()}"
            self.overlay.jogada(uci[:2], uci[2:4], detalhe)
        self._mostrar_destaques(uci)

    def _mostrar_destaques(self, uci):
        if self.destaques is None or self.coords_tabuleiro is None or self._grade is None:
            return
        try:
            janela_chrome = self._janela_chrome()
            if janela_chrome is None or gw.getActiveWindow() != janela_chrome:
                self.destaques.ocultar()
                return
            self.destaques.mostrar(
                chess.Move.from_uci(uci), self.cor_jogador, self.coords_tabuleiro,
                self._grade, self._origem_monitor,
            )
        except (OSError, tk.TclError) as erro:
            self.destaques.ocultar()
            self.destaques = None
            print(f"Marcações  | Falha ao exibir: {erro}")

    def _atualizar_tk(self):
        if self.overlay is not None:
            try:
                self.overlay.update()
            except Exception:
                pass

    # -----------------------------------------------------------------------
    # CHROME / MONITOR / CAPTURA
    # -----------------------------------------------------------------------
    def _janela_chrome(self):
        try:
            janelas = gw.getWindowsWithTitle("Chrome")
            if not janelas:
                janelas = gw.getWindowsWithTitle("chromium")
            janelas = [
                janela for janela in janelas if janela.width > 400 and janela.height > 300
            ]
            if not janelas:
                return None
            return max(
                janelas,
                key=lambda janela: (
                    "chess.com" in janela.title.lower(),
                    janela.width * janela.height,
                ),
            )
        except Exception:
            return None

    def focar_chrome(self):
        janela = self._janela_chrome()
        if janela is None:
            print("Chrome     | Janela ainda não encontrada")
            return False
        try:
            if janela.isMinimized:
                janela.restore()
            janela.activate()
            return True
        except Exception as erro:
            print(f"Chrome     | Falha ao trazer a janela para frente: {erro}")
            return False

    def _detectar_monitor_chrome(self):
        janela = self._janela_chrome()
        if janela is None:
            return 1
        try:
            centro_x = janela.left + janela.width // 2
            centro_y = janela.top + janela.height // 2
            with _abrir_mss() as sct:
                for indice, monitor in enumerate(sct.monitors):
                    if indice == 0:
                        continue
                    if (
                        monitor["left"] <= centro_x < monitor["left"] + monitor["width"]
                        and monitor["top"] <= centro_y < monitor["top"] + monitor["height"]
                    ):
                        return indice
        except Exception:
            pass
        return 1

    def capturar_tela(self):
        if self.destaques is not None:
            self.destaques.preparar_captura()
        with _abrir_mss() as sct:
            monitores = sct.monitors
            indice = self.monitor_indice if 0 < self.monitor_indice < len(monitores) else 1
            monitor = monitores[indice]
            self._origem_monitor = (monitor["left"], monitor["top"])
            imagem = np.array(sct.grab(monitor))
            return cv2.cvtColor(imagem, cv2.COLOR_BGRA2BGR)

    # -----------------------------------------------------------------------
    # LOOP PRINCIPAL
    # -----------------------------------------------------------------------
    def executar(self):
        if not self.focar_chrome():
            print("Chrome     | Mantenha o tabuleiro visível")
        self.monitor_indice = self._detectar_monitor_chrome()
        self._posicionar_overlay()
        self._registrar_log(f"--- sessão iniciada | monitor={self.monitor_indice} ---")
        print("\nTabuleiro  | Procurando no Chrome")
        ultima_regiao = None
        ultima_grade = None
        ultima_leitura = None

        try:
            while self.partida_ativa:
                imagem_tela = self.capturar_tela()

                if self.coords_tabuleiro is None:
                    coords = deteccao.encontrar_tabuleiro_automatico(imagem_tela)
                    if coords is not None and deteccao.verificar_tabuleiro_valido(imagem_tela, coords):
                        self.coords_tabuleiro = coords
                        self._marcar_leitura_estavel()
                        self._posicionar_overlay()
                        left, top, largura, altura = coords
                        print(f"Tabuleiro  | Encontrado em ({left}, {top}) · {largura} × {altura} px")
                    else:
                        self._exibir_espera("Aguardando tabuleiro", "Abra o tabuleiro no Chrome")
                        self._status("Tabuleiro  | Aguardando tabuleiro visível")
                        self._dormir()
                        continue
                else:
                    if not deteccao.verificar_tabuleiro_valido(imagem_tela, self.coords_tabuleiro):
                        self._falhas_tabuleiro += 1
                        if self._falhas_tabuleiro >= 3:
                            print("Tabuleiro  | Saiu da tela. Procurando novamente.")
                            self.coords_tabuleiro = None
                            self._falhas_tabuleiro = 0
                            self._resetar_posicao()
                            self.cor_jogador = None
                            self.monitor_indice = self._detectar_monitor_chrome()
                            self._posicionar_overlay()
                        self._exibir_espera("Aguardando tabuleiro", "Mantenha o tabuleiro visível")
                        self._dormir()
                        continue
                    self._falhas_tabuleiro = 0

                left, top, largura, altura = self.coords_tabuleiro
                regiao = imagem_tela[top:top + altura, left:left + largura]
                if regiao.size == 0:
                    self.coords_tabuleiro = None
                    self._exibir_espera("Aguardando tabuleiro", "Mantenha o tabuleiro visível")
                    self._dormir()
                    continue

                if self._grade is None or self._grade_coords != self.coords_tabuleiro:
                    self._grade = deteccao.calibrar_grade(regiao)
                    self._grade_coords = self.coords_tabuleiro
                    print(
                        f"Leitura    | Grade calibrada: casa={self._grade.tamanho:.2f}px "
                        f"origem=({self._grade.origem_x:.1f}, {self._grade.origem_y:.1f}) "
                        f"score={self._grade.pontuacao:.3f}"
                    )
                    if self._grade.pontuacao < 0.90:
                        print(
                            "Leitura    | Calibração fraca. Confira se o tabuleiro "
                            "está inteiro na tela."
                        )

                if self._grade != ultima_grade or not np.array_equal(regiao, ultima_regiao):
                    ultima_leitura = deteccao.detectar_pecas(
                        regiao, self.referencias, LIMIAR_TEMPLATE, self._grade
                    )
                    ultima_regiao = regiao.copy()
                    ultima_grade = self._grade
                grade, confiancas = ultima_leitura
                if len(grade) < MIN_PECAS_LEITURA:
                    self._pecas_pendentes = None
                    self._marcar_leitura_instavel()
                    self._poucas_pecas += 1
                    if self._poucas_pecas >= 3:
                        self._grade = None
                        self._poucas_pecas = 0
                    self._status("Leitura    | Poucas peças detectadas")
                    self._exibir_espera("Relendo posição", "Poucas peças detectadas", "Leitura")
                    self._dormir()
                    continue
                self._poucas_pecas = 0

                orientacao = self._orientacao_manual
                if orientacao is None:
                    orientacao = deteccao.detectar_orientacao(regiao, self._grade)
                if orientacao is None:
                    orientacao = self.cor_jogador
                if orientacao is None:
                    orientacao = deteccao.detectar_cor_jogador(grade)
                pecas = deteccao.mapear_pecas(grade, orientacao)

                valido, motivo = deteccao.validar_posicao(pecas)
                if not valido:
                    self._pecas_pendentes = None
                    self._marcar_leitura_instavel()
                    self._exibir_espera("Relendo posição", motivo.capitalize(), "Leitura")
                    chave_diag = tuple(sorted(pecas.items()))
                    agora = time.monotonic()
                    if (
                        chave_diag != self._ultimo_diag
                        and agora - self._ultimo_diag_em > 3.0
                    ):
                        self._ultimo_diag = chave_diag
                        self._ultimo_diag_em = agora
                        self._salvar_diagnostico(regiao, grade, confiancas, motivo)
                        self._registrar_log(
                            f"posição rejeitada: {motivo} | {len(grade)} peças | "
                            f"{deteccao.montar_board(pecas, chess.WHITE).fen()}"
                        )
                        self._grade = None
                    self._status(
                        f"Leitura    | Posição inconsistente ({motivo})"
                    )
                    self._dormir()
                    continue
                self._ultimo_diag = None

                if orientacao != self.cor_jogador:
                    self._resetar_posicao()
                    self.cor_jogador = orientacao
                    nome_cor = "Brancas" if orientacao == chess.WHITE else "Pretas"
                    print(f"Tabuleiro  | {nome_cor} embaixo")

                nova_posicao = pecas != self.pecas_estaveis
                if nova_posicao and pecas != self._pecas_pendentes:
                    self._marcar_leitura_instavel()
                    self._pecas_pendentes = pecas
                    self._exibir_espera("Confirmando posição", estado="Leitura")
                    self._status("Leitura    | Confirmando posição")
                    self._dormir()
                    continue

                board_anterior = self.board
                if nova_posicao or self.board is None:
                    destaques = deteccao.detectar_lances_destacados(
                        regiao, self._grade, grade, orientacao
                    )
                    if not self._confirmar_posicao(pecas, destaques):
                        self._marcar_leitura_instavel()
                        self._exibir_espera("Relendo posição", "Lance ainda não identificado", "Leitura")
                        self._status("Leitura    | Lance ainda não identificado")
                        self._dormir()
                        continue
                self._pecas_pendentes = None
                self._marcar_leitura_estavel()

                board = self.board
                if board is None:
                    self._status("Leitura    | Vez indefinida. Aguardando um lance visível.")
                    self._exibir_espera("Aguardando um lance", "Vez ainda indefinida")
                    self._dormir()
                    continue
                mudou = board is not board_anterior
                if mudou:
                    self.contador_partida_encerrada = 0
                    vez = "Brancas" if board.turn == chess.WHITE else "Pretas"
                    print(f"Posição    | {len(pecas)} peças · Vez das {vez.lower()}")
                    self._registrar_log(
                        f"posição confirmada: {len(pecas)} peças | vez={vez} | "
                        f"{board.fen()}"
                    )
                    if MODO_DEBUG:
                        lista = ", ".join(
                            f"{deteccao.SIMBOLOS.get(s, s)} {c}" for c, s in sorted(pecas.items())
                        )
                        print(f"      {lista}")

                if board.is_game_over():
                    self._exibir_espera("Fim de partida", estado="Encerrada")
                    self.contador_partida_encerrada += 1
                    if self.contador_partida_encerrada >= CONFIRMACOES_FIM:
                        self._imprimir_fim(board)
                        break
                    self._status(
                        f"Posição    | Confirmando fim de partida "
                        f"({self.contador_partida_encerrada}/{CONFIRMACOES_FIM})..."
                    )
                    self._dormir(INTERVALO_CONFIRMACAO_FIM)
                    continue
                self.contador_partida_encerrada = 0

                if board.turn == self.cor_jogador:
                    if mudou:
                        self._exibir_espera("Calculando jogada", estado="Stockfish")
                        self._atualizar_tk()
                        print("Stockfish  | Calculando jogada")
                    jogada_uci, descricao = self.calcular_melhor_jogada(board)
                    if jogada_uci and descricao:
                        if descricao != self.jogada_anterior:
                            print(f"Sugestão   | {descricao}  [{jogada_uci}]")
                            self.jogada_anterior = descricao
                            self._registrar_log(
                                f"jogada sugerida: {jogada_uci} | {descricao}"
                            )
                        self._exibir_jogada(board, jogada_uci)
                    else:
                        self._status("Stockfish  | Nenhuma jogada recebida")
                        self._exibir_espera("Aguardando Stockfish", estado="Motor")
                else:
                    self._status("Posição    | Aguardando oponente")
                    self._exibir_espera("Aguardando oponente", estado="Vez do oponente")

                self._dormir()

        except KeyboardInterrupt:
            print("\nSessão     | Interrompida pelo usuário")
        except Exception as erro:
            print(f"\nSessão     | Erro inesperado: {erro}")
            import traceback
            traceback.print_exc()
        finally:
            self._encerrar_engine()
            if self.overlay is not None:
                try:
                    self.overlay.destroy()
                except Exception:
                    pass
            print("Sessão     | Encerrada")

    def _confirmar_posicao(self, pecas, lances_destacados):
        candidatos = deteccao.tabuleiros_possiveis(pecas)
        if not candidatos:
            return False

        houve_transicao = self.pecas_estaveis is not None and pecas != self.pecas_estaveis
        board = None
        if houve_transicao:
            anteriores = (
                [self.board] if self.board is not None
                else deteccao.tabuleiros_possiveis(self.pecas_estaveis)
            )
            board = deteccao.avancar_posicao(anteriores, candidatos[0])
            if board is None and not deteccao.transicao_plausivel(self.pecas_estaveis, pecas):
                return False

        if board is None:
            board = deteccao.sincronizar_pelo_destaque(candidatos, lances_destacados)
            if board is None and houve_transicao:
                # Após uma leitura conhecida, um salto sem destaque confiável
                # só pode virar a nova base depois da ressincronização.
                return False
            if board is None and len(candidatos) == 1:
                board = candidatos[0]

        self.board = board
        self.pecas_estaveis = pecas
        return True

    def _resetar_posicao(self):
        self.board = None
        self.pecas_estaveis = None
        self._pecas_pendentes = None
        self.jogada_anterior = None
        self.contador_partida_encerrada = 0

    def _registrar_log(self, mensagem):
        try:
            with open(ARQUIVO_LOG, "a", encoding="utf-8") as arquivo:
                arquivo.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} | {mensagem}\n")
        except Exception:
            pass

    def _marcar_leitura_estavel(self):
        self._instavel_desde = None

    def _marcar_leitura_instavel(self):
        agora = time.monotonic()
        if self._instavel_desde is None:
            self._instavel_desde = agora
            return
        if agora - self._instavel_desde < TEMPO_RESINCRONIZACAO:
            return
        self._instavel_desde = None
        print("Leitura    | Ressincronizando posição")
        self._registrar_log("ressincronização automática após leituras instáveis")
        self._resetar_posicao()
        self._grade = None
        self._ultimo_diag = None
        self._poucas_pecas = 0

    def _imprimir_fim(self, board):
        if board.is_checkmate():
            vencedor = "Pretas" if board.turn == chess.WHITE else "Brancas"
            print(f"Partida    | Xeque-mate · Vitória das {vencedor.lower()}")
        elif board.is_stalemate():
            print("Partida    | Empate por afogamento")
        elif board.is_insufficient_material():
            print("Partida    | Empate por material insuficiente")
        elif board.is_seventyfive_moves():
            print("Partida    | Empate pela regra dos 75 lances")
        elif board.is_fivefold_repetition():
            print("Partida    | Empate por repetição quíntupla")
        else:
            print("Partida    | Encerrada")

    def _salvar_diagnostico(self, regiao, grade, confiancas, motivo):
        nome = time.strftime("debug_leitura_%Y%m%d_%H%M%S.png")
        imagem = regiao.copy()
        if self._grade is not None:
            tamanho = self._grade.tamanho
            origem_x = self._grade.origem_x
            origem_y = self._grade.origem_y
        else:
            tamanho = min(imagem.shape[:2]) / 8.0
            origem_x = 0.0
            origem_y = 0.0
        for (linha, coluna), simbolo in grade.items():
            x = int(origem_x + coluna * tamanho + tamanho / 2) - 8
            y = int(origem_y + linha * tamanho + tamanho / 2) + 6
            cv2.putText(
                imagem, simbolo, (x, y), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2
            )
        cv2.imwrite(nome, imagem)
        print(f"Leitura    | Diagnóstico salvo: {nome} ({motivo})")
        baixas = sorted(
            ((linha, coluna), round(valor, 2))
            for (linha, coluna), valor in confiancas.items()
            if valor < 0.6
        )
        if baixas:
            print(f"      Confianças baixas (<0.6): {baixas}")

    def _status(self, mensagem):
        if mensagem != self._mensagem_status:
            print(mensagem)
            self._mensagem_status = mensagem

    def _dormir(self, intervalo=INTERVALO_LEITURA):
        self._atualizar_tk()
        time.sleep(intervalo)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Analisa o tabuleiro de xadrez visível no Chrome.")
    parser.add_argument(
        "--orientacao", choices=("brancas", "pretas"),
        help="cor das peças na parte de baixo, caso as coordenadas estejam ocultas",
    )
    parser.add_argument(
        "--tempo-stockfish-ms", type=int, default=TEMPO_STOCKFISH_MS,
        help=f"tempo de análise por jogada em milissegundos (padrão {TEMPO_STOCKFISH_MS})",
    )
    opcoes = parser.parse_args()
    if opcoes.tempo_stockfish_ms < 1:
        parser.error("--tempo-stockfish-ms deve ser maior que zero")
    orientacao = {"brancas": chess.WHITE, "pretas": chess.BLACK}.get(opcoes.orientacao)
    assistente = AssistenteXadrez(orientacao, opcoes.tempo_stockfish_ms)
    assistente.executar()
