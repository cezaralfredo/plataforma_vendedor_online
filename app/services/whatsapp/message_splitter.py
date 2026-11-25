import re

# EMVCo BACEN PIX Copia e Cola começa com 000201 e termina com tag CRC16 6304XXXX (pode conter espaços no nome/cidade)
_PIX_BRCODE_RE = re.compile(r"`{0,3}(000201[^\r\n`]{20,}?6304[0-9A-Fa-f]{4})`{0,3}")
_LIST_ITEM_RE = re.compile(r"^\s*(?:[-*•▪▫✅🔹🔸👉]|\d+[.)])\s+")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?…])\s+(?=[A-ZÁÉÍÓÚÂÊÔÃÕÇ0-9\"'(“*#🎉✨😊🙏💖👉])")


def _split_long_paragraph(paragraph: str, max_chars: int) -> list[str]:
    """Divide um parágrafo longo sem quebrar URLs ou palavras no meio."""
    text = paragraph.strip()
    if len(text) <= max_chars:
        return [text] if text else []

    # 1. Se tiver múltiplas quebras de linha simples, agrupa por linhas respeitando limite
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if len(lines) > 1:
        # Mantém listas compactas juntas se não forem gigantes
        is_list_block = sum(1 for ln in lines if _LIST_ITEM_RE.match(ln)) >= 2
        if is_list_block and len(text) <= max_chars + 180:
            return [text]

        groups: list[str] = []
        current = ""
        for ln in lines:
            candidate = f"{current}\n{ln}" if current else ln
            if len(candidate) <= max_chars or not current:
                current = candidate
            else:
                groups.append(current)
                current = ln
        if current:
            groups.append(current)
        if len(groups) > 1:
            return groups

    # 2. Divide por fronteiras de frase
    sentences = [s.strip() for s in _SENTENCE_SPLIT_RE.split(text) if s.strip()]
    if len(sentences) > 1:
        groups = []
        current = ""
        for sent in sentences:
            candidate = f"{current} {sent}" if current else sent
            if len(candidate) <= max_chars or not current:
                current = candidate
            else:
                groups.append(current)
                current = sent
        if current:
            groups.append(current)
        return groups

    return [text]


def _split_prose_segment(segment: str, max_chunk_chars: int) -> list[str]:
    raw_paragraphs = [p.strip() for p in re.split(r"\n\s*\n+", segment.strip()) if p.strip()]
    if not raw_paragraphs:
        return []

    # Mantém título/introdução terminado em ":" colado à lista seguinte quando couber
    glued: list[str] = []
    idx = 0
    while idx < len(raw_paragraphs):
        para = raw_paragraphs[idx]
        if (
            idx + 1 < len(raw_paragraphs)
            and para.endswith(":")
            and _LIST_ITEM_RE.match(raw_paragraphs[idx + 1])
            and len(para) + 2 + len(raw_paragraphs[idx + 1]) <= max_chunk_chars + 180
        ):
            glued.append(f"{para}\n{raw_paragraphs[idx + 1]}")
            idx += 2
        else:
            glued.append(para)
            idx += 1

    pieces: list[str] = []
    for para in glued:
        pieces.extend(_split_long_paragraph(para, max_chunk_chars))
    return pieces


def split_whatsapp_message(
    text: str,
    *,
    max_chunk_chars: int = 320,
    short_message_threshold: int = 240,
    max_chunks: int = 4,
) -> list[str]:
    """
    Divide uma resposta longa em 2 a 4 mensagens naturais de WhatsApp:
    - Mensagens curtas (<= 240 chars e sem código PIX isolável) permanecem em 1 bolha.
    - Códigos PIX Copia e Cola (000201...) são isolados em bolha própria para facilitar
      copiar com um toque no celular, sem crases nem texto colado.
    - Blocos de texto são agrupados de forma equilibrada sem exceder `max_chunks`
      (exceto quando há bolha atômica de PIX, que preserva sua integridade).
    """
    cleaned = (text or "").strip()
    if not cleaned:
        return []

    # Extrai segmentos intercalando prosa e códigos PIX Copia e Cola atômicos
    segments: list[tuple[bool, str]] = []  # (is_atomic_pix, content)
    last_end = 0
    has_pix = False
    for match in _PIX_BRCODE_RE.finditer(cleaned):
        has_pix = True
        before = cleaned[last_end : match.start()].strip()
        if before:
            segments.append((False, before))
        pix_code = match.group(1).strip()
        segments.append((True, pix_code))
        last_end = match.end()

    tail = cleaned[last_end:].strip()
    if tail:
        segments.append((False, tail))

    # Mensagem curta sem PIX -> envia em 1 bloco só
    if not has_pix and len(cleaned) <= short_message_threshold:
        return [cleaned]

    # Expande cada segmento de prosa em blocos naturais
    blocks: list[tuple[bool, str]] = []
    for is_atomic, content in segments:
        if is_atomic:
            blocks.append((True, content))
        else:
            for piece in _split_prose_segment(content, max_chunk_chars):
                blocks.append((False, piece))

    if not blocks:
        return [cleaned]

    # Agrupa blocos adjacentes muito curtos (sem misturar com PIX atômico nem absorver pergunta final longa)
    merged: list[tuple[bool, str]] = []
    for i, (is_atomic, content) in enumerate(blocks):
        if is_atomic or not merged or merged[-1][0]:
            merged.append((is_atomic, content))
            continue

        prev_atomic, prev_content = merged[-1]
        is_last = i == len(blocks) - 1
        is_closing_question = is_last and content.endswith("?") and len(cleaned) > 280
        combined_len = len(prev_content) + 2 + len(content)

        if (
            not prev_atomic
            and not is_closing_question
            and combined_len <= max_chunk_chars
            and (len(prev_content) < 120 or len(content) < 90)
        ):
            merged[-1] = (False, f"{prev_content}\n\n{content}")
        else:
            merged.append((False, content))

    # Limita a quantidade total de bolhas mesclando os dois blocos de prosa adjacentes mais curtos
    effective_max = max_chunks + (1 if has_pix else 0)
    while len(merged) > effective_max:
        best_idx = -1
        best_len = 10**9
        for idx in range(len(merged) - 1):
            if merged[idx][0] or merged[idx + 1][0]:
                continue
            cand_len = len(merged[idx][1]) + len(merged[idx + 1][1])
            if cand_len < best_len:
                best_len = cand_len
                best_idx = idx
        if best_idx < 0:
            break
        merged[best_idx] = (
            False,
            f"{merged[best_idx][1]}\n\n{merged[best_idx + 1][1]}",
        )
        merged.pop(best_idx + 1)

    return [content for _, content in merged if content]


def compute_typing_delay(chunk: str) -> float:
    """Calcula pausa humana proporcional ao tamanho da próxima bolha (0.7s a 1.8s)."""
    if not chunk:
        return 0.0
    if chunk.startswith("000201"):
        return 0.6
    return round(min(1.8, max(0.7, len(chunk) / 160.0)), 2)
