# workers.py
from __future__ import annotations

import multiprocessing as mp
import signal
import time
from typing import Optional

from prmoney_worker import run_prmoney_worker
from agent import run_agent  # твоя существующая функция запуска агента


# Глобальные процессы
_prmoney_proc: Optional[mp.Process] = None
_agent_proc: Optional[mp.Process] = None


# ============================================================
# PRMONEY WORKER
# ============================================================

def _prmoney_worker_entry():
    """
    Точка входа для процесса PrMoney.
    Бесконечно крутит run_prmoney_worker().
    """
    print("[PrMoneyWorker] Старт процесса")
    # Нормально реагируем на CTRL+C / SIGTERM
    signal.signal(signal.SIGTERM, lambda *args: exit(0))

    try:
        run_prmoney_worker()
    except KeyboardInterrupt:
        print("[PrMoneyWorker] Остановлен по KeyboardInterrupt")
    except Exception as e:
        print(f"[PrMoneyWorker] Ошибка в воркере: {e}")


def start_prmoney_worker() -> None:
    global _prmoney_proc

    if _prmoney_proc is not None and _prmoney_proc.is_alive():
        print("[ADMIN] PrMoney worker уже запущен.")
        return

    proc = mp.Process(target=_prmoney_worker_entry, daemon=True)
    proc.start()
    _prmoney_proc = proc
    print(f"[ADMIN] PrMoney worker запущен, pid={proc.pid}")


def stop_prmoney_worker() -> None:
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
    return _prmoney_proc is not None and _prmoney_proc.is_alive()


# ============================================================
# AGENT WORKER
# ============================================================

def _agent_worker_entry():
    """
    Точка входа для процесса агента.
    """
    print("[AgentWorker] Старт процесса Aideon Agent")
    signal.signal(signal.SIGTERM, lambda *args: exit(0))

    try:
        # твой существующий бесконечный цикл
        run_agent()
    except KeyboardInterrupt:
        print("[AgentWorker] Остановлен по KeyboardInterrupt")
    except Exception as e:
        print(f"[AgentWorker] Ошибка в воркере: {e}")


def start_agent_worker() -> None:
    global _agent_proc

    if _agent_proc is not None and _agent_proc.is_alive():
        print("[ADMIN] Agent worker уже запущен.")
        return

    proc = mp.Process(target=_agent_worker_entry, daemon=True)
    proc.start()
    _agent_proc = proc
    print(f"[ADMIN] Agent worker запущен, pid={proc.pid}")


def stop_agent_worker() -> None:
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
    return _agent_proc is not None and _agent_proc.is_alive()