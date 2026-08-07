"""
Utilitário de linha de comando para gerenciar clientes localmente,
sem precisar montar chamadas HTTP na mão.

Uso:
    python manage_clients.py criar --conta 8823456 --nome "Fulano"
    python manage_clients.py listar
    python manage_clients.py revogar --token <token>
    python manage_clients.py ativar  --token <token>
    python manage_clients.py limpar-sinais
"""

import argparse

import storage


def main():
    parser = argparse.ArgumentParser(description="Gerencia clientes do signal copier.")
    sub = parser.add_subparsers(dest="comando", required=True)

    p_criar = sub.add_parser("criar", help="Cria um novo cliente e gera o token dele.")
    p_criar.add_argument("--conta", required=True, help="Número da conta MT5.")
    p_criar.add_argument("--nome", default="", help="Nome/identificação do cliente (opcional).")

    sub.add_parser("listar", help="Lista todos os clientes cadastrados.")

    p_revogar = sub.add_parser("revogar", help="Revoga o acesso de um cliente.")
    p_revogar.add_argument("--token", required=True)

    p_ativar = sub.add_parser("ativar", help="Reativa o acesso de um cliente.")
    p_ativar.add_argument("--token", required=True)

    sub.add_parser(
        "limpar-sinais",
        help="Esvazia a fila de sinais (mantém o contador de id avançando -- seguro pra fazer com EAs já rodando).",
    )

    args = parser.parse_args()
    storage.init_db()

    if args.comando == "criar":
        token = storage.create_client(args.conta, args.nome)
        print(f"Cliente criado.\n  conta: {args.conta}\n  nome:  {args.nome}\n  token: {token}")

    elif args.comando == "listar":
        clientes = storage.list_clients()
        if not clientes:
            print("Nenhum cliente cadastrado ainda.")
        for c in clientes:
            status = "ativo" if c["active"] else "REVOGADO"
            print(f"[{status}] conta={c['account_id']} nome={c['nome']!r} token={c['token']}")

    elif args.comando == "revogar":
        ok = storage.set_client_active(args.token, active=False)
        print("Revogado." if ok else "Token não encontrado.")

    elif args.comando == "ativar":
        ok = storage.set_client_active(args.token, active=True)
        print("Reativado." if ok else "Token não encontrado.")

    elif args.comando == "limpar-sinais":
        n = storage.clear_signals()
        print(f"Fila de sinais esvaziada -- {n} sinal(is) removido(s). "
              "O contador de id continua de onde estava, então EAs já rodando não são afetados.")


if __name__ == "__main__":
    main()
