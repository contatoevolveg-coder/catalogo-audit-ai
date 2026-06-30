import requests
import json
import sys

API_URL = "http://localhost:8000"

def main():
    print("[+] INICIANDO TESTE END-TO-END DE ENDPOINTS...")
    
    # 1. Verifica se a API está de pé
    try:
        r = requests.get(f"{API_URL}/products")
        if r.status_code == 200:
            print("[+] API FastAPI ativa e respondendo.")
        else:
            print(f"[-] Erro ao conectar: Status {r.status_code}")
            sys.exit(1)
    except Exception as e:
        print(f"[-] Falha na conexão com {API_URL}: {e}")
        sys.exit(1)

    # 2. Importa as listagens de teste
    print("\n[1] Importando listagens de teste...")
    r = requests.post(f"{API_URL}/products/import-test-listings")
    print(f"    Status: {r.status_code} | Resposta: {r.json()}")

    # 3. Lista os produtos cadastrados
    r = requests.get(f"{API_URL}/products")
    products = r.json()
    print(f"    Produtos cadastrados no banco: {len(products)}")

    if not products:
        print("[-] ERRO: Nenhum produto cadastrado.")
        sys.exit(1)

    # 4. Audita o primeiro produto (Fone de Ouvido no Mercado Livre)
    p1 = products[-1]  # O primeiro produto importado (geralmente no final da lista desc)
    print(f"\n[2] Auditando o Produto ID {p1['id']} ('{p1['title']}') no {p1['marketplace'].upper()}...")
    
    r = requests.post(f"{API_URL}/products/{p1['id']}/audit")
    if r.status_code == 200:
        suggestion_1 = r.json()
        print(f"    Auditoria realizada. Score de SEO: {suggestion_1['seo_score']}/100")
        print(f"    Sugestão de Título: '{suggestion_1['suggested_title']}'")
        
        # 5. Aprova as sugestões do primeiro produto
        print(f"\n[3] Aprovando sugestões da Auditoria ID {suggestion_1['id']}...")
        r_app = requests.post(f"{API_URL}/suggestions/{suggestion_1['id']}/approve")
        print(f"    Status: {r_app.status_code} | Novo status do produto: {r_app.json().get('status')}")
    else:
        print(f"    Erro na auditoria: {r.text}")

    # 6. Audita o segundo produto (Camiseta na Shopee)
    if len(products) >= 2:
        p2 = products[-2]
        print(f"\n[4] Auditando o Produto ID {p2['id']} ('{p2['title']}') na {p2['marketplace'].upper()}...")
        r = requests.post(f"{API_URL}/products/{p2['id']}/audit")
        if r.status_code == 200:
            suggestion_2 = r.json()
            print(f"    Auditoria realizada. Score de SEO: {suggestion_2['seo_score']}/100")
            
            # 7. Rejeita as sugestões do segundo produto
            print(f"\n[5] Rejeitando sugestões da Auditoria ID {suggestion_2['id']}...")
            r_rej = requests.post(f"{API_URL}/suggestions/{suggestion_2['id']}/reject")
            print(f"    Status: {r_rej.status_code} | Novo status da sugestão: {r_rej.json().get('status')}")
        else:
            print(f"    Erro na auditoria: {r.text}")

    # 8. Audita os produtos restantes em lote
    print("\n[6] Solicitando auditoria em lote para os demais produtos pendentes...")
    r_batch = requests.post(f"{API_URL}/products/audit-all")
    print(f"    Status: {r_batch.status_code} | Resposta: {r_batch.json()}")

    # 9. Lista logs finais de auditoria
    print("\n[7] Carregando logs de auditoria e tokens...")
    r_logs = requests.get(f"{API_URL}/logs")
    logs = r_logs.json()
    print(f"    Total de logs de auditoria registrados: {len(logs)}")
    for log in logs:
        print(f"      - ID {log['id']} | Prod ID {log['product_id']} | Tokens In: {log['tokens_input']} | Out: {log['tokens_output']} | Latência: {log['latency_seconds']:.2f}s")
        
    print("\n[+] TESTE DE ENDPOINTS CONCLUÍDO COM SUCESSO!")

if __name__ == "__main__":
    main()
