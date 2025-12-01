# workers.py
from __future__ import annotations

import multiprocessing as mp
import signal
from typing import Optional, Dict

from prmoney_worker import run_prmoney_worker   # воркер-ПРОВАЙДЕР: только читает PrMoney и пишет в БД
from agent import run_agent                     # твой существующий Aideon Agent, обрабатывает инвойсы из БД


# Попытка задать start_method один раз (важно для macOS/Windows, особенно с FastAPI)
try:
    mp.set_start_method("spawn")
except RuntimeError:
    # Уже был установлен где-то раньше — игнорируем
    pass


# Глобальные процессы (живут в рамках одного процесса FastAPI / админки)
_prmoney_proc: Optional[mp.Process] = None
_agent_proc: Optional[mp.Process] = None


# ============================================================
# PRMONEY WORKER
# ============================================================

def _prmoney_worker_entry() -> None:
    """
    Точка входа для процесса PrMoney.

    ВАЖНО:
      - этот воркер НЕ трогает Multitransfer и НЕ играет роль Aideon Agent;
      - его единственная задача — периодически ходить в PrMoney API,
        забирать новые инвойсы и сохранять/обновлять их в БД.

    Вся дальнейшая логика (Multitransfer, Vision, диплинки, вебхуки) —
    в отдельном процессе Aideon Agent (run_agent).
    """
    print("[PrMoneyWorker] Старт процесса PrMoney worker")
    signal.signal(signal.SIGTERM, lambda *args: exit(0))

    try:
        run_prmoney_worker()  # синхронная бесконечная петля внутри prmoney_worker.py
    except KeyboardInterrupt:
        print("[PrMoneyWorker] Остановлен по KeyboardInterrupt")
    except Exception as e:
        print(f"[PrMoneyWorker] Ошибка в воркере: {e}")


def start_prmoney_worker() -> None:
    """
    Запуск отдельного процесса опроса PrMoney.
    Если уже запущен — ничего не делаем.
    """
    global _prmoney_proc

    if _prmoney_proc is not None and _prmoney_proc.is_alive():
        print("[ADMIN] PrMoney worker уже запущен.")
        return

    proc = mp.Process(target=_prmoney_worker_entry, daemon=True)
    proc.start()
    _prmoney_proc = proc
    print(f"[ADMIN] PrMoney worker запущен, pid={proc.pid}")


def stop_prmoney_worker() -> None:
    """
    Остановка процесса PrMoney worker, если он запущен.
    """
    global _prmoney_proc

    if _prmoney_proc is None:
        print("[ADMIN] PrMoney worker не запущен.")
        return

    if _prmoney_proc.is_alive():
        print(f"[ADMIN] Останавливаю PrMoney worker pid={_prmoney_proc.pid}...")
        _prmoney_proc.terminate()
        _prmoney_proc.join(timeout=5)
        print("[ADMIN] PrMoney worker остановлен.")
    else:
        print("[ADMIN] PrMoney worker уже был мёртв.")

    _prmoney_proc = None


def is_prmoney_worker_alive() -> bool:
    """
    Жив ли процесс PrMoney worker в рамках текущего процесса админки.
    """
    return _prmoney_proc is not None and _prmoney_proc.is_alive()


# ============================================================
# AGENT WORKER (Aideon Agent)
# ============================================================

def _agent_worker_entry() -> None:
    """
    Точка входа для процесса Aideon Agent.

    Здесь крутится твой существующий агент:
      - читает инвойсы из БД (status=queued и т.п.),
      - гоняет Multitransfer,
      - ждёт капчи, Vision, диплинки, шлёт вебхуки и т.д.

    НИКАКИХ прямых запросов в PrMoney отсюда нет — агент работает
    только с локальной БД.
    """
    print("[AgentWorker] Старт процесса Aideon Agent")
    signal.signal(signal.SIGTERM, lambda *args: exit(0))

    try:
        run_agent()  # твой текущий бесконечный цикл агента
    except KeyboardInterrupt:
        print("[AgentWorker] Остановлен по KeyboardInterrupt")
    except Exception as e:
        print(f"[AgentWorker] Ошибка в воркере: {e}")


def start_agent_worker() -> None:
    """
    Запуск процесса Aideon Agent.
    Если уже запущен — ничего не делаем.
    """
    global _agent_proc

    if _agent_proc is not None and _agent_proc.is_alive():
        print("[ADMIN] Agent worker уже запущен.")
        return

    proc = mp.Process(target=_agent_worker_entry, daemon=True)
    proc.start()
    _agent_proc = proc
    print(f"[ADMIN] Agent worker запущен, pid={proc.pid}")


def stop_agent_worker() -> None:
    """
    Остановка процесса Aideon Agent, если он запущен.
    """
    global _agent_proc

    if _agent_proc is None:
        print("[ADMIN] Agent worker не запущен.")
        return

    if _agent_proc.is_alive():
        print(f"[ADMIN] Останавливаю Agent worker pid={_agent_proc.pid}...")
        _agent_proc.terminate()
        _agent_proc.join(timeout=5)
        print("[ADMIN] Agent worker остановлен.")
    else:
        print("[ADMIN] Agent worker уже был мёртв.")

    _agent_proc = None


def is_agent_worker_alive() -> bool:
    """
    Жив ли процесс Aideon Agent в рамках текущего процесса админки.
    """
    return _agent_proc is not None and _agent_proc.is_alive()


# ============================================================
# УТИЛИТА ДЛЯ АДМИНКИ
# ============================================================

def get_workers_status() -> Dict[str, str]:
    """
    Упрощённый статус для вывода в админке:
      {
        "prmoney": "running"/"stopped",
        "agent":   "running"/"stopped",
      }
    """
    return {
        "prmoney": "running" if is_prmoney_worker_alive() else "stopped",
        "agent": "running" if is_agent_worker_alive() else "stopped",
    }