from __future__ import annotations

from db import SessionLocal
from models import Proxy


def list_proxies():
    db = SessionLocal()
    try:
        proxies = db.query(Proxy).order_by(Proxy.id.asc()).all()
        if not proxies:
            print("Прокси пока нет.")
            return
        print("\nID | ACTIVE | FAILS | LABEL | ADDRESS")
        print("-" * 80)
        for p in proxies:
            print(
                f"{p.id:2d} | "
                f"{'YES' if p.is_active else ' NO'}   | "
                f"{p.fail_count:5d} | "
                f"{p.label or '-':10s} | "
                f"{p.address}"
            )
        print()
    finally:
        db.close()


def add_proxy():
    address = input("Введите полный адрес прокси (http://user:pass@host:port): ").strip()
    label = input("Метка/страна (например RU, MX, GLOBAL): ").strip() or None

    db = SessionLocal()
    try:
        exists = db.query(Proxy).filter(Proxy.address == address).first()
        if exists:
            print("Такой прокси уже есть.")
            return
        p = Proxy(address=address, label=label, is_active=True, fail_count=0)
        db.add(p)
        db.commit()
        print("Прокси добавлен.")
    finally:
        db.close()


def toggle_proxy():
    proxy_id = input("ID прокси для включения/выключения: ").strip()
    if not proxy_id.isdigit():
        print("Некорректный ID.")
        return
    proxy_id = int(proxy_id)

    db = SessionLocal()
    try:
        p = db.query(Proxy).filter(Proxy.id == proxy_id).first()
        if not p:
            print("Прокси не найден.")
            return
        p.is_active = not p.is_active
        db.commit()
        print(f"Прокси {p.id} теперь is_active={p.is_active}")
    finally:
        db.close()


def delete_proxy():
    proxy_id = input("ID прокси для удаления: ").strip()
    if not proxy_id.isdigit():
        print("Некорректный ID.")
        return
    proxy_id = int(proxy_id)

    db = SessionLocal()
    try:
        p = db.query(Proxy).filter(Proxy.id == proxy_id).first()
        if not p:
            print("Прокси не найден.")
            return
        db.delete(p)
        db.commit()
        print("Прокси удалён.")
    finally:
        db.close()


def main_menu():
    while True:
        print("""
====== ПРОКСИ-МЕНЕДЖЕР AIDEON AGENT ======
1) Показать список прокси
2) Добавить прокси
3) Включить/выключить прокси
4) Удалить прокси
0) Выход
""")
        choice = input("Выберите пункт: ").strip()

        if choice == "1":
            list_proxies()
        elif choice == "2":
            add_proxy()
        elif choice == "3":
            toggle_proxy()
        elif choice == "4":
            delete_proxy()
        elif choice == "0":
            break
        else:
            print("Неизвестный пункт меню.")


if __name__ == "__main__":
    main_menu()