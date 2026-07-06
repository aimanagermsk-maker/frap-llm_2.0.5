import fitz  # PyMuPDF
import base64
import requests

OLLAMA_URL = "http://195.209.210.141:11434/api/generate"

def pdf_page_to_base64(pdf_path, page_num):
    """Открывает PDF, конвертирует страницу в base64-encoded PNG."""
    doc = fitz.open(pdf_path)
    page = doc.load_page(page_num)
    pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))  # Масштабируем для лучшего качества
    image_bytes = pix.tobytes("png")
    return base64.b64encode(image_bytes).decode('utf-8')

def send_image_to_ollama(image_b64, prompt="Что ты видишь на этом документе?", model="qwen3-vl:30b"):
    headers = {"Content-Type": "application/json"}
    data = {
        "model": model,
        "prompt": prompt,
        "images": [image_b64],
        "stream": False
    }
    response = requests.post(OLLAMA_URL, json=data, headers=headers)
    if response.status_code == 200:
        return response.json().get("response", "")
    else:
        raise Exception(f"Ошибка от Ollama: {response.text}")

def process_pdf_pages(pdf_path, prompt="Опиши содержимое страницы."):
    doc = fitz.open(pdf_path)
    total_pages = doc.page_count
    print(f"Обнаружено страниц: {total_pages}")
    
    full_response = ""
    for i in range(total_pages):
        print(f"Обработка страницы {i + 1}...")
        img_b64 = pdf_page_to_base64(pdf_path, i)
        result = send_image_to_ollama(img_b64, prompt=prompt)
        full_response += f"\n--- Страница {i + 1} ---\n{result}\n"
    
    return full_response

if __name__ == "__main__":
    pdf_file = "1.pdf"  # Укажите путь к вашему PDF-файлу
    try:
        answer = process_pdf_pages(pdf_file)
        print("\n=== Результат ===")
        print(answer)
    except Exception as e:
        print(f"[ERROR] {e}")
