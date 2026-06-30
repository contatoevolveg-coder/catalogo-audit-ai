import os
import json
import sys
from dotenv import load_dotenv

# Adiciona o diretório raiz do backend ao sys.path para podermos importar os módulos
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.app.agent import audit_product_with_gemini
from backend.app.schemas import GeminiAuditResponse

def main():
    # Carrega variáveis de ambiente (.env)
    dotenv_path = os.path.join(os.path.dirname(__file__), ".env")
    load_dotenv(dotenv_path)
    
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key or api_key == "sua_chave_aqui":
        print("[-] ERRO: GEMINI_API_KEY não está configurada no arquivo backend/.env!")
        print("    Por favor, abra o arquivo 'backend/.env' e insira uma chave válida do Google AI Studio.")
        sys.exit(1)
        
    print(f"[+] Chave do Gemini encontrada (iniciando com {api_key[:5]}...)")
    print(f"[+] Modelo configurado: {os.getenv('GEMINI_MODEL', 'gemini-3.5-flash')}")
    
    # Carrega test_listings.json
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    test_file_path = os.path.join(project_root, "test_listings.json")
    
    if not os.path.exists(test_file_path):
        print(f"[-] ERRO: test_listings.json não encontrado em: {test_file_path}")
        sys.exit(1)
        
    with open(test_file_path, "r", encoding="utf-8") as f:
        products = json.load(f)
        
    print(f"\n[+] Carregados {len(products)} produtos de teste de {test_file_path}")
    print("=" * 80)
    
    for i, p in enumerate(products):
        print(f"\nAUDITANDO PRODUTO {i+1}/{len(products)}:")
        print(f" - Marketplace: {p['marketplace'].upper()}")
        print(f" - Título Original: '{p['title']}'")
        print(f" - Preço: R$ {p['price']:.2f}")
        
        try:
            # Chama o agente de auditoria
            result, tokens_in, tokens_out, latency = audit_product_with_gemini(
                title=p["title"],
                description=p["description"],
                images=p["images"],
                category=p["category"],
                price=p["price"],
                marketplace=p["marketplace"]
            )
            
            # Validação do tipo retornado
            assert isinstance(result, GeminiAuditResponse), "O retorno não é uma instância de GeminiAuditResponse"
            
            print("\n [+] RESULTADO DA AUDITORIA (JSON ESTRUTURADO VÁLIDO):")
            print(f"   -> Novo Título Sugerido: '{result.suggested_title}'")
            print(f"   -> Score de SEO: {result.seo_score}/100")
            
            print("   -> Atributos Faltantes Recomendados:")
            if result.missing_attributes:
                for attr in result.missing_attributes:
                    print(f"      * {attr.name}: '{attr.recommended_value}' (Motivo: {attr.reason})")
            else:
                print("      * Nenhum")
                
            print("   -> Problemas de Imagem Identificados:")
            if result.image_issues:
                for issue in result.image_issues:
                    print(f"      * [{issue.severity}] Na imagem '{issue.image_url[:40]}...': {issue.issue}")
            else:
                print("      * Nenhum")
                
            print(f"   -> Latência: {latency:.2f}s")
            print(f"   -> Tokens de Consumo: {tokens_in} de Entrada, {tokens_out} de Saída")
            
        except Exception as e:
            print(f" [-] ERRO ao auditar produto: {e}")
            
        print("-" * 80)

if __name__ == "__main__":
    main()
