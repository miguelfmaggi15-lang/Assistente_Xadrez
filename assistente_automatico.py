"""
ASSISTENTE DE XADREZ - 100% AUTOMÁTICO
========================================
1. Foca o Chrome já aberto
2. Detecta o tabuleiro na tela automaticamente
3. Reconhece as peças por template matching invariante ao fundo
4. Rastreia o turno pelo diff entre posições
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

import io
import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk

import chess
import cv2
import mss
import numpy as np
import pygetwindow as gw

import deteccao

if isinstance(sys.stdout, io.TextIOWrapper):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

INTERVALO_LEITURA = 1.0
PROFUNDIDADE_STOCKFISH = 15
TEMPO_STOCKFISH_MS = 1500
TIMEOUT_STOCKFISH = 8.0
LIMIAR_TEMPLATE = 0.40
CONFIRMACOES_FIM = 5
MIN_PECAS_LEITURA = 6
MIN_PECAS_FIM = 3
TEMPO_RESINCRONIZACAO = 6.0
MODO_DEBUG = False
NOME_HELPER = "stockfish_helper.js"
ARQUIVO_LOG = "debug_sessao.log"


def _abrir_mss():
    return mss.MSS() if hasattr(mss, "MSS") else mss.mss()


class AssistenteXadrez:
    def __init__(self):
        print("=" * 60)
        print("  ASSISTENTE DE XADREZ - 100% AUTOMÁTICO")
        print("=" * 60)
        print()

        print("🔧 Inicializando Stockfish (via Node.js)...")
        self.engine_proc = None
        self._fila_respostas = queue.Queue()
        self._req_id = 0
        self._ultimo_fen_enviado = None
        self._jogada_cache_uci = None
        self._jogada_cache_desc = None
        self._iniciar_engine()

        print("🔧 Carregando referências das peças...")
        self.referencias = deteccao.carregar_referencias()

        self.cor_jogador = None
        self.turno = chess.WHITE
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
        self.lbl_titulo = None
        self.lbl_jogada = None
        self.lbl_status = None
        self._criar_overlay()

        print()
        print("📋 INSTRUÇÕES:")
        print("   1. Deixe o Chrome aberto com o chess.com")
        print("   2. Inicie uma partida contra bots")
        print("   3. O programa detecta tudo automaticamente")
        print("   4. Sua cor = peças na parte de baixo da tela")
        print("   5. Pressione Ctrl+C para parar a qualquer momento")
        print()
        print("⚠️  Não minimize o Chrome enquanto o programa roda!")
        print()

    # -----------------------------------------------------------------------
    # STOCKFISH (helper Node.js)
    # -----------------------------------------------------------------------
    def _iniciar_engine(self, anunciar=True):
        self._encerrar_engine()
        try:
            ambiente = os.environ.copy()
            ambiente["SF_DEPTH"] = str(PROFUNDIDADE_STOCKFISH)
            ambiente["SF_MOVETIME"] = str(TEMPO_STOCKFISH_MS)
            self.engine_proc = subprocess.Popen(
                ["node", NOME_HELPER],
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
            time.sleep(0.4)
            if anunciar:
                print("   ✅ Stockfish carregado (Node.js helper)")
            return True
        except Exception as erro:
            print(f"   ❌ Erro ao iniciar Stockfish: {erro}")
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
            print(f"   ⚠️  Falha ao enviar posição ao Stockfish: {erro}")
            self._iniciar_engine(anunciar=False)
            return None, None

        prazo = time.time() + TIMEOUT_STOCKFISH
        jogada = None
        while True:
            restante = prazo - time.time()
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
            print("   ⚠️  Stockfish demorou muito; reiniciando motor...")
            self._iniciar_engine(anunciar=False)
            return None, None

        if jogada in ("none", "(none)"):
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
            self.overlay = tk.Tk()
            self.overlay.attributes("-topmost", True)
            self.overlay.overrideredirect(True)
            self.overlay.configure(bg="#1a1a2e")
            self.overlay.geometry("350x130+100+100")

            self.lbl_titulo = tk.Label(
                self.overlay,
                text="♟️ MELHOR JOGADA",
                font=("Arial", 13, "bold"),
                bg="#1a1a2e",
                fg="#e94560",
            )
            self.lbl_titulo.pack(pady=(8, 4))

            self.lbl_jogada = tk.Label(
                self.overlay,
                text="Aguardando...",
                font=("Arial", 18, "bold"),
                bg="#1a1a2e",
                fg="#4cc9f0",
            )
            self.lbl_jogada.pack(pady=(0, 6))

            self.lbl_status = tk.Label(
                self.overlay,
                text="● Ativo",
                font=("Arial", 10),
                bg="#1a1a2e",
                fg="#4ade80",
            )
            self.lbl_status.pack()

            self.overlay.update_idletasks()
            self.overlay.update()
        except Exception as erro:
            print(f"   ⚠️  Não foi possível criar overlay: {erro}")
            self.overlay = None

    def _posicionar_overlay(self):
        if self.overlay is None:
            return
        try:
            with _abrir_mss() as sct:
                if not (0 < self.monitor_indice < len(sct.monitors)):
                    return
                monitor = sct.monitors[self.monitor_indice]
            largura = 350
            x = monitor["left"] + monitor["width"] - largura - 20
            y = monitor["top"] + 20
            self.overlay.geometry(f"{largura}x130+{x}+{y}")
            self.overlay.update()
        except Exception:
            pass

    def atualizar_overlay(self, jogada_texto, uci=""):
        if self.overlay is None or self.lbl_jogada is None:
            return
        try:
            self.lbl_jogada.config(text=jogada_texto)
            if uci and self.lbl_titulo is not None:
                self.lbl_titulo.config(text=f"♟️ {uci}")
            self.overlay.update()
        except Exception:
            pass

    def ocultar_overlay(self):
        if self.overlay is None:
            return
        try:
            if self.lbl_jogada is not None:
                self.lbl_jogada.config(text="Aguardando oponente...")
            self.overlay.update()
        except Exception:
            pass

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
            return max(janelas, key=lambda janela: janela.width * janela.height)
        except Exception:
            return None

    def focar_chrome(self):
        janela = self._janela_chrome()
        if janela is None:
            print("   ⚠️  Chrome não encontrado!")
            return False
        try:
            if janela.isMinimized:
                janela.restore()
            janela.activate()
            time.sleep(1.0)
            return True
        except Exception as erro:
            print(f"   ⚠️  Erro ao focar Chrome: {erro}")
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
        with _abrir_mss() as sct:
            monitores = sct.monitors
            indice = self.monitor_indice if 0 < self.monitor_indice < len(monitores) else 1
            imagem = np.array(sct.grab(monitores[indice]))
            return cv2.cvtColor(imagem, cv2.COLOR_BGRA2BGR)

    # -----------------------------------------------------------------------
    # LOOP PRINCIPAL
    # -----------------------------------------------------------------------
    def executar(self):
        print("🚀 Iniciando assistente automático...")
        print()
        if not self.focar_chrome():
            print("   ⚠️  Mantenha o Chrome visível na tela.")
        self.monitor_indice = self._detectar_monitor_chrome()
        self._posicionar_overlay()
        self._registrar_log(f"--- sessão iniciada | monitor={self.monitor_indice} ---")
        print("🔍 Procurando tabuleiro na tela...\n")

        try:
            while self.partida_ativa:
                imagem_tela = self.capturar_tela()

                if self.coords_tabuleiro is None:
                    coords = deteccao.encontrar_tabuleiro_automatico(imagem_tela)
                    if coords is not None and deteccao.verificar_tabuleiro_valido(imagem_tela, coords):
                        self.coords_tabuleiro = coords
                        self._marcar_leitura_estavel()
                        left, top, largura, altura = coords
                        print(f"   ✅ Tabuleiro encontrado: ({left}, {top}) {largura}x{altura}")
                    else:
                        self._status("   ⏳ Aguardando tabuleiro aparecer na tela...")
                        self._dormir()
                        continue
                else:
                    if not deteccao.verificar_tabuleiro_valido(imagem_tela, self.coords_tabuleiro):
                        self._falhas_tabuleiro += 1
                        if self._falhas_tabuleiro >= 3:
                            print("   ⚠️  Tabuleiro não está mais visível. Procurando novamente...")
                            self.coords_tabuleiro = None
                            self._falhas_tabuleiro = 0
                            self._resetar_posicao()
                        self._dormir()
                        continue
                    self._falhas_tabuleiro = 0

                left, top, largura, altura = self.coords_tabuleiro
                regiao = imagem_tela[top:top + altura, left:left + largura]
                if regiao.size == 0:
                    self.coords_tabuleiro = None
                    self._dormir()
                    continue

                if self._grade is None or self._grade_coords != self.coords_tabuleiro:
                    self._grade = deteccao.calibrar_grade(regiao)
                    self._grade_coords = self.coords_tabuleiro
                    print(
                        f"   📐 Grade calibrada: casa={self._grade.tamanho:.2f}px "
                        f"origem=({self._grade.origem_x:.1f}, {self._grade.origem_y:.1f}) "
                        f"score={self._grade.pontuacao:.3f}"
                    )
                    if self._grade.pontuacao < 0.90:
                        print(
                            "   ⚠️  Calibração fraca: confira se o tabuleiro "
                            "está inteiro na tela."
                        )

                grade, confiancas = deteccao.detectar_pecas(
                    regiao, self.referencias, LIMIAR_TEMPLATE, self._grade
                )
                if len(grade) < MIN_PECAS_LEITURA:
                    self._marcar_leitura_instavel()
                    self._poucas_pecas += 1
                    if self._poucas_pecas >= 3:
                        self._grade = None
                        self._poucas_pecas = 0
                    self._status("   ⏳ Poucas peças detectadas. Aguardando...")
                    self._dormir()
                    continue
                self._poucas_pecas = 0

                if self.cor_jogador is None:
                    self.cor_jogador = deteccao.detectar_cor_jogador(grade)
                    if self.cor_jogador is None:
                        self._status("   ⏳ Não foi possível detectar sua cor. Aguardando...")
                        self._dormir()
                        continue
                    nome_cor = "Brancas" if self.cor_jogador == chess.WHITE else "Pretas"
                    print(f"   ✅ Sua cor detectada: {nome_cor} (peças na parte de baixo)\n")

                pecas = deteccao.mapear_pecas(grade, self.cor_jogador)

                valido, motivo = deteccao.validar_posicao(pecas)
                if not valido:
                    self._marcar_leitura_instavel()
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
                        f"   ⚠️  Posição inconsistente ({motivo}). Aguardando..."
                    )
                    self._dormir()
                    continue
                self._ultimo_diag = None

                mudou = False
                if self.pecas_estaveis is None:
                    self.pecas_estaveis = dict(pecas)
                    self.turno = self._turno_inicial(pecas)
                    mudou = True
                elif pecas == self.pecas_estaveis:
                    self._pecas_pendentes = None
                elif pecas == self._pecas_pendentes:
                    if not deteccao.transicao_plausivel(self.pecas_estaveis, pecas):
                        self._marcar_leitura_instavel()
                        self._pecas_pendentes = None
                        self._registrar_log(
                            f"leitura descartada: nenhuma peça nova | {len(pecas)} peças"
                        )
                        self._status(
                            "   ⚠️  Peça sumiu sem lance correspondente. Relendo..."
                        )
                        self._dormir()
                        continue
                    lado = deteccao.inferir_lado_movido(self.pecas_estaveis, pecas)
                    self.pecas_estaveis = dict(pecas)
                    self._pecas_pendentes = None
                    if lado is not None:
                        self.turno = not lado
                    else:
                        self.turno = not self.turno
                        self._registrar_log(
                            "lado do lance indefinido; assumindo que quem estava "
                            "para jogar fez o lance"
                        )
                    mudou = True
                else:
                    self._marcar_leitura_instavel()
                    self._pecas_pendentes = dict(pecas)
                    self._status("   ⏳ Confirmando nova posição...")
                    self._dormir()
                    continue

                board = deteccao.montar_board(pecas, self.turno)
                if not board.is_valid():
                    board_alternativo = deteccao.montar_board(pecas, not self.turno)
                    if board_alternativo.is_valid():
                        self.turno = not self.turno
                        board = board_alternativo
                    else:
                        self._marcar_leitura_instavel()
                        self._status("   ⚠️  Posição inválida detectada. Aguardando...")
                        self._dormir()
                        continue
                self._marcar_leitura_estavel()

                if mudou:
                    vez = "Brancas" if self.turno == chess.WHITE else "Pretas"
                    print(f"   📍 {len(pecas)} peças | Vez: {vez}")
                    self._registrar_log(
                        f"posição confirmada: {len(pecas)} peças | vez={vez} | "
                        f"{board.fen()}"
                    )
                    if MODO_DEBUG:
                        lista = ", ".join(
                            f"{deteccao.SIMBOLOS.get(s, s)} {c}" for c, s in sorted(pecas.items())
                        )
                        print(f"      {lista}")

                if len(pecas) >= MIN_PECAS_FIM and board.is_game_over():
                    self.contador_partida_encerrada += 1
                    if self.contador_partida_encerrada >= CONFIRMACOES_FIM:
                        self._imprimir_fim(board)
                        break
                    self._status(
                        f"   ⚠️  Possível fim de partida "
                        f"({self.contador_partida_encerrada}/{CONFIRMACOES_FIM})..."
                    )
                    self._dormir()
                    continue
                self.contador_partida_encerrada = 0

                if self.turno == self.cor_jogador:
                    if mudou:
                        print("   🤔 Calculando melhor jogada...")
                    jogada_uci, descricao = self.calcular_melhor_jogada(board)
                    if jogada_uci and descricao:
                        if descricao != self.jogada_anterior:
                            print("\n" + "🎯" * 35)
                            print(f"   ➡️  MELHOR JOGADA: {descricao}")
                            print(f"   📐 UCI: {jogada_uci}")
                            print("🎯" * 35 + "\n")
                            self.jogada_anterior = descricao
                            self._registrar_log(
                                f"jogada sugerida: {jogada_uci} | {descricao}"
                            )
                        self.atualizar_overlay(descricao, jogada_uci)
                    else:
                        self._status("   ⚠️  Stockfish não retornou jogada.")
                else:
                    self._status("   ⏸️  Aguardando jogada do oponente...")
                    self.ocultar_overlay()

                self._dormir()

        except KeyboardInterrupt:
            print("\n\n👋 Programa interrompido pelo usuário.")
        except Exception as erro:
            print(f"\n❌ Erro inesperado: {erro}")
            import traceback
            traceback.print_exc()
        finally:
            self._encerrar_engine()
            if self.overlay is not None:
                try:
                    self.overlay.destroy()
                except Exception:
                    pass
            print("👋 Assistente encerrado.")

    def _turno_inicial(self, pecas):
        if pecas == deteccao.POSICAO_INICIAL:
            return chess.WHITE
        if self.cor_jogador is not None:
            return self.cor_jogador
        return chess.WHITE

    def _resetar_posicao(self):
        self.pecas_estaveis = None
        self._pecas_pendentes = None
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
        print("   🔄 Leituras instáveis por muito tempo; ressincronizando posição...")
        self._registrar_log("ressincronização automática após leituras instáveis")
        self._resetar_posicao()
        self._grade = None
        self._ultimo_diag = None
        self._poucas_pecas = 0

    def _imprimir_fim(self, board):
        print("\n" + "=" * 60)
        print("  🏁 PARTIDA ENCERRADA!")
        print("=" * 60)
        if board.is_checkmate():
            vencedor = "Pretas" if board.turn == chess.WHITE else "Brancas"
            print(f"   Xeque-mate! Vencedor: {vencedor}")
        elif board.is_stalemate():
            print("   Empate por afogamento!")
        elif board.is_insufficient_material():
            print("   Empate por material insuficiente!")
        elif board.is_seventyfive_moves():
            print("   Empate pela regra dos 75 lances!")
        elif board.is_fivefold_repetition():
            print("   Empate por repetição quíntupla!")
        else:
            print("   Partida encerrada.")
        print("=" * 60)

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
        print(f"   🖼️  Diagnóstico salvo: {nome} ({motivo})")
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

    def _dormir(self):
        self._atualizar_tk()
        time.sleep(INTERVALO_LEITURA)


if __name__ == "__main__":
    assistente = AssistenteXadrez()
    assistente.executar()
