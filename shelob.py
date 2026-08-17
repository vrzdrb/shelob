#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
shelob - массовая замена в .yml/.yaml с поддержкой токенов <any>.
"""

import os
import re
import shutil
import locale

YAML_EXTS = ('.yml', '.yaml')

ANY_TOKEN = "<any>"
ANY_N_RE = re.compile(r"<any_(\d+)>")
MAX_ANY_INDEX = 10

# ANSI colors: acid yellow / lime
CSI = "\033["
RESET = CSI + "0m"
ACID = CSI + "38;5;226m"
LIME = CSI + "38;5;190m"
RED = CSI + "38;5;196m"
BOLD = CSI + "1m"
DIM = CSI + "2m"

try:
    import curses
    CURSES_AVAILABLE = True
except ImportError:
    CURSES_AVAILABLE = False


def safe_curses_addstr(win, y, x, s, attr=0, maxlen=None):
    try:
        h, w = win.getmaxyx()
        if y < 0 or y >= h or x < 0 or x >= w:
            return
        if maxlen is None:
            maxlen = max(0, w - x - 1)
        if maxlen <= 0:
            return
        win.addnstr(y, x, s, maxlen, attr)
    except Exception:
        pass


class EditorPane:
    def __init__(self, title):
        self.title = title
        self.lines = [""]
        self.cy = 0
        self.cx = 0
        self.top = 0
        self.left = 0
        self.win = None

    def set_win(self, win):
        self.win = win

    def text(self):
        return "\n".join(self.lines)

    def clamp_cursor(self):
        if self.cy < 0:
            self.cy = 0
        if self.cy >= len(self.lines):
            self.cy = len(self.lines) - 1
        line = self.lines[self.cy]
        if self.cx < 0:
            self.cx = 0
        if self.cx > len(line):
            self.cx = len(line)

    def insert_char(self, ch):
        if not ch:
            return
        line = self.lines[self.cy]
        self.lines[self.cy] = line[:self.cx] + ch + line[self.cx:]
        self.cx += len(ch)

    def insert_text(self, s):
        s = s.replace('\r\n', '\n').replace('\r', '\n')
        for ch in s:
            if ch == '\n':
                self.insert_newline()
            elif ch == '\t':
                self.insert_char('  ')
            elif ch.isprintable():
                self.insert_char(ch)

    def insert_newline(self):
        line = self.lines[self.cy]
        self.lines[self.cy] = line[:self.cx]
        self.lines.insert(self.cy + 1, line[self.cx:])
        self.cy += 1
        self.cx = 0

    def backspace(self):
        if self.cx > 0:
            line = self.lines[self.cy]
            self.lines[self.cy] = line[:self.cx - 1] + line[self.cx:]
            self.cx -= 1
        elif self.cy > 0:
            prev_line = self.lines[self.cy - 1]
            cur_line = self.lines[self.cy]
            self.lines[self.cy - 1] = prev_line + cur_line
            del self.lines[self.cy]
            self.cy -= 1
            self.cx = len(prev_line)

    def delete_char(self):
        line = self.lines[self.cy]
        if self.cx < len(line):
            self.lines[self.cy] = line[:self.cx] + line[self.cx + 1:]
        elif self.cy + 1 < len(self.lines):
            self.lines[self.cy] += self.lines[self.cy + 1]
            del self.lines[self.cy + 1]

    def ensure_visible(self):
        if self.win is None:
            return
        h, w = self.win.getmaxyx()
        view_h = max(1, h - 2)
        view_w = max(1, w - 2)
        if self.top < 0:
            self.top = 0
        if self.cy < self.top:
            self.top = self.cy
        if self.cy >= self.top + view_h:
            self.top = self.cy - view_h + 1
        if self.top >= len(self.lines):
            self.top = max(0, len(self.lines) - 1)
        line_len = len(self.lines[self.cy])
        if self.cx > line_len:
            self.cx = line_len
        if self.left < 0:
            self.left = 0
        if self.cx < self.left:
            self.left = self.cx
        if self.cx >= self.left + view_w:
            self.left = self.cx - view_w + 1

    def draw(self, active, colors):
        if self.win is None:
            return
        self.clamp_cursor()
        self.ensure_visible()
        self.win.erase()
        attr = colors['active'] if active else colors['inactive']
        try:
            self.win.attrset(attr)
            self.win.border()
            self.win.attrset(0)
        except Exception:
            pass
        h, w = self.win.getmaxyx()
        title = f" {self.title} "
        if w > len(title) + 4:
            x = max(1, (w - len(title)) // 2)
            safe_curses_addstr(self.win, 0, x, title, attr, max(0, w - x - 1))
        view_h = max(0, h - 2)
        view_w = max(0, w - 2)
        for row in range(view_h):
            idx = self.top + row
            if idx >= len(self.lines):
                break
            line = self.lines[idx]
            start = self.left if active else 0
            text = line[start:start + view_w]
            safe_curses_addstr(self.win, 1 + row, 1, text, 0, view_w)
        if active:
            y = 1 + self.cy - self.top
            x = 1 + self.cx - self.left
            try:
                self.win.move(y, x)
            except Exception:
                pass

    def handle_key(self, ch):
        if ch in ('\n', '\r', 10, 13) or ch == getattr(curses, 'KEY_ENTER', 343):
            self.insert_newline()
            return
        if ch in ('\b', '\x7f', 8, 127) or ch == getattr(curses, 'KEY_BACKSPACE', 263):
            self.backspace()
            return
        if ch == getattr(curses, 'KEY_DC', 330):
            self.delete_char()
            return
        if ch == getattr(curses, 'KEY_LEFT', 260):
            if self.cx > 0:
                self.cx -= 1
            elif self.cy > 0:
                self.cy -= 1
                self.cx = len(self.lines[self.cy])
            return
        if ch == getattr(curses, 'KEY_RIGHT', 261):
            line_len = len(self.lines[self.cy])
            if self.cx < line_len:
                self.cx += 1
            elif self.cy < len(self.lines) - 1:
                self.cy += 1
                self.cx = 0
            return
        if ch == getattr(curses, 'KEY_UP', 259):
            if self.cy > 0:
                self.cy -= 1
                self.cx = min(self.cx, len(self.lines[self.cy]))
            return
        if ch == getattr(curses, 'KEY_DOWN', 258):
            if self.cy < len(self.lines) - 1:
                self.cy += 1
                self.cx = min(self.cx, len(self.lines[self.cy]))
            return
        if ch == getattr(curses, 'KEY_HOME', 262):
            self.cx = 0
            return
        if ch == getattr(curses, 'KEY_END', 360):
            self.cx = len(self.lines[self.cy])
            return
        if isinstance(ch, str):
            self.insert_text(ch)


def setup_colors():
    colors = {'header': 0, 'active': 0, 'inactive': 0, 'status': 0}
    if not curses.has_colors():
        return colors
    try:
        curses.start_color()
    except Exception:
        pass
    try:
        curses.use_default_colors()
    except Exception:
        pass
    acid = curses.COLOR_YELLOW
    lime = curses.COLOR_YELLOW
    try:
        if curses.COLORS >= 256:
            acid = 226
            lime = 190
    except Exception:
        pass

    def init_pair(pair_no, fg, bg):
        try:
            curses.init_pair(pair_no, fg, bg)
            return curses.color_pair(pair_no)
        except Exception:
            try:
                curses.init_pair(pair_no, curses.COLOR_YELLOW, 0)
                return curses.color_pair(pair_no)
            except Exception:
                return 0

    colors['header'] = init_pair(1, acid, -1)
    colors['active'] = init_pair(2, acid, -1) | getattr(curses, 'A_BOLD', 0)
    colors['inactive'] = init_pair(3, lime, -1) | getattr(curses, 'A_DIM', 0)
    colors['status'] = init_pair(4, curses.COLOR_BLACK, acid) | getattr(curses, 'A_BOLD', 0)
    return colors


def draw_header(stdscr, w, colors):
    header_lines = [
        "<any_1>...<any_10> в НАЙТИ — захват; в ЗАМЕНИТЬ — подстановка",
    ]
    attr = colors['header'] | getattr(curses, 'A_BOLD', 0)
    for i, line in enumerate(header_lines):
        safe_curses_addstr(stdscr, i, 0, line, attr, w - 1)


def swallow_escape_sequence(stdscr, ch):
    if ch not in ('\x1b', 27):
        return False
    stdscr.nodelay(True)
    try:
        nxt = stdscr.get_wch()
        if nxt == '[':
            while True:
                c = stdscr.get_wch()
                if c is None:
                    break
                if isinstance(c, str) and len(c) == 1 and '@' <= c <= '~':
                    break
                if isinstance(c, int):
                    break
    except Exception:
        pass
    finally:
        stdscr.nodelay(False)
    return True


def split_ui(stdscr):
    try:
        curses.curs_set(1)
    except Exception:
        pass
    stdscr.keypad(True)
    try:
        mask = getattr(curses, 'BUTTON1_CLICKED', 0)
        mask |= getattr(curses, 'REPORT_MOUSE_POSITION', 0)
        curses.mousemask(mask)
    except Exception:
        pass

    colors = setup_colors()
    panes = [EditorPane("НАЙТИ"), EditorPane("ЗАМЕНИТЬ")]
    active = 0
    win_left = win_right = None
    last_dim = None

    while True:
        h, w = stdscr.getmaxyx()
        header_h = 1
        status_h = 1
        if h < 10 or w < 40:
            stdscr.erase()
            safe_curses_addstr(
                stdscr, 0, 0,
                "Терминал слишком мал. Увеличьте окно или нажмите Ctrl+Q.",
                colors.get('status', 0), w - 1
            )
            stdscr.refresh()
            ch = stdscr.get_wch()
            if ch in ('\x11', 17):
                return None
            continue

        pan_h = h - header_h - status_h
        left_w = w // 2
        right_w = w - left_w
        dim = (pan_h, left_w, right_w, header_h)

        if win_left is None or dim != last_dim:
            win_left = curses.newwin(pan_h, left_w, header_h, 0)
            win_right = curses.newwin(pan_h, right_w, header_h, left_w)
            last_dim = dim

        panes[0].set_win(win_left)
        panes[1].set_win(win_right)

        stdscr.erase()
        draw_header(stdscr, w, colors)
        panes[0].draw(active == 0, colors)
        panes[1].draw(active == 1, colors)

        status = " Tab — окно | Ctrl+Shift+C/V — коп./вставка | Ctrl+X — замена | Ctrl+Q — выход "
        safe_curses_addstr(stdscr, h - 1, 0, status.ljust(w - 1), colors['status'], w - 1)

        stdscr.noutrefresh()
        if win_left:
            win_left.noutrefresh()
        if win_right:
            win_right.noutrefresh()
        if active == 0 and win_left:
            win_left.noutrefresh()
        elif win_right:
            win_right.noutrefresh()
        curses.doupdate()

        try:
            ch = stdscr.get_wch()
        except KeyboardInterrupt:
            raise
        except Exception:
            ch = None

        if ch is None:
            continue
        if ch in ('\x11', 17):
            return None
        if ch in ('\x18', 24):
            return panes[0].text(), panes[1].text()
        if ch in ('\t', 9):
            active = 1 - active
            continue
        if ch == getattr(curses, 'KEY_MOUSE', 409):
            try:
                _, mx, my, _, _ = curses.getmouse()
                if header_h <= my < header_h + pan_h:
                    active = 0 if mx < left_w else 1
            except Exception:
                pass
            continue
        if ch == getattr(curses, 'KEY_RESIZE', 410):
            last_dim = None
            continue
        if swallow_escape_sequence(stdscr, ch):
            continue
        panes[active].handle_key(ch)


def prompt_multiline_fallback(title):
    print(f"{ACID}{BOLD}{title}{RESET}")
    print(f"{DIM}Вставьте/введите текст. Завершите ввод строкой с точкой '.' и нажмите Enter.{RESET}")
    lines = []
    while True:
        try:
            line = input()
        except EOFError:
            break
        if line == ".":
            break
        lines.append(line)
    return "\n".join(lines)


def fallback_ui():
    print(f"{ACID}{BOLD}Shelob{RESET}")
    print(f"{LIME}Разделённый режим недоступен, используется последовательный ввод.{RESET}")
    print()
    find_text = prompt_multiline_fallback("Фрагмент для поиска (поддерживаются <any_1>...<any_10>)")
    replace_text = prompt_multiline_fallback("Фрагмент для замены (<any_N> подставит захваченное значение)")
    return find_text, replace_text


def prompt_exclusions():
    print()
    print(f"{ACID}{BOLD}Исключения{RESET}")
    try:
        raw = input("Имена файлов через пробел (пусто — без исключений): ").strip()
    except EOFError:
        raw = ""
    return raw.split() if raw else []


def find_yaml_files(root):
    matches = []
    for dirpath, dirnames, filenames in os.walk(root):
        if 'backups' in dirnames:
            dirnames.remove('backups')
        for fn in filenames:
            if fn.lower().endswith(YAML_EXTS):
                matches.append(os.path.join(dirpath, fn))
    return matches


def should_exclude(path, exclude_list):
    if not exclude_list:
        return False
    return os.path.basename(path) in exclude_list


def make_backup(path, root, log_lines):
    backup_root = os.path.join(root, 'backups')
    rel = os.path.relpath(path, root)
    dest = os.path.join(backup_root, rel)
    dest_dir = os.path.dirname(dest)
    try:
        os.makedirs(dest_dir, exist_ok=True)
        shutil.copyfile(path, dest)
        log_lines.append(f"Бэкап создан: {dest}")
        return True, dest
    except Exception as e:
        log_lines.append(f"Не удалось создать бэкап для {path}: {e}")
        return False, None


def validate_and_build_patterns(find_text, replace_text):
    """
    Валидирует токены <any>/<any_N> и строит regex.
    Возвращает (compiled_regex, normalized_replace_template, error_message).
    Если токенов нет — (None, None, None).
    """
    # КРИТИЧЕСКОЕ ИСПРАВЛЕНИЕ: нормализуем ОБА поля
    normalized_find = find_text.replace(ANY_TOKEN, "<any_0>")
    normalized_replace = replace_text.replace(ANY_TOKEN, "<any_0>")

    find_indices = set()
    for m in ANY_N_RE.finditer(normalized_find):
        idx = int(m.group(1))
        if idx > MAX_ANY_INDEX:
            return None, None, f"Токен <any_{idx}> превышает лимит {MAX_ANY_INDEX}."
        find_indices.add(idx)

    replace_indices = set()
    for m in ANY_N_RE.finditer(normalized_replace):
        idx = int(m.group(1))
        if idx > MAX_ANY_INDEX:
            return None, None, f"Токен <any_{idx}> в замене превышает лимит {MAX_ANY_INDEX}."
        replace_indices.add(idx)

    if not find_indices and not replace_indices:
        return None, None, None

    missing = replace_indices - find_indices
    if missing:
        tokens = ", ".join(f"<any_{i}>" for i in sorted(missing))
        return None, None, f"В замене есть токены, которых нет в поиске: {tokens}"

    # Строим regex
    parts = ANY_N_RE.split(normalized_find)
    pattern_parts = [re.escape(parts[0])]
    for i in range(1, len(parts), 2):
        idx_str = parts[i]
        next_literal = parts[i + 1] if i + 1 < len(parts) else ""

        group_name = f"any_{idx_str}"
        if next_literal == "" or next_literal.startswith("\n"):
            wildcard = r"[^\n]*"
        else:
            wildcard = r"[^\n]+?"

        pattern_parts.append(f"(?P<{group_name}>{wildcard})")
        pattern_parts.append(re.escape(next_literal))

    full_pattern = "".join(pattern_parts)
    try:
        compiled = re.compile(full_pattern)
    except re.error as e:
        return None, None, f"Ошибка regex: {e}"

    # Возвращаем нормализованный шаблон замены
    return compiled, normalized_replace, None


def process_replacements(root, find_text, replace_text, exclude_list, log_lines):
    files = find_yaml_files(root)
    total = len(files)
    processed = 0
    skipped = []
    not_found = []
    errors = []

    log_lines.append(f"Найдено YAML файлов: {total}")

    if find_text == "":
        log_lines.append("Пропущено: пустой фрагмент поиска.")
        log_lines.append("=== РЕЗЮМЕ ===")
        log_lines.append(f"Обработано: 0 из {total}")
        return {"total": total, "processed": 0, "skipped": skipped, "not_found": not_found, "errors": errors}

    regex, normalized_replace_template, validation_error = validate_and_build_patterns(find_text, replace_text)

    if validation_error:
        log_lines.append(f"ОШИБКА ШАБЛОНА: {validation_error}")
        log_lines.append("Обработка отменена.")
        log_lines.append("=== РЕЗЮМЕ ===")
        log_lines.append(f"Обработано: 0 из {total}")
        return {"total": total, "processed": 0, "skipped": skipped, "not_found": not_found, "errors": errors}

    use_any_mode = regex is not None
    if use_any_mode:
        log_lines.append("Режим <any>: включён")

        def replacement_callback(match):
            result = normalized_replace_template
            # Заменяем токены в шаблоне замены на захваченные значения
            for m in ANY_N_RE.finditer(normalized_replace_template):
                token = m.group(0)
                idx = m.group(1)
                group_name = f"any_{idx}"
                try:
                    captured = match.group(group_name)
                except IndexError:
                    captured = token
                # Заменяем только первое вхождение этого токена за раз
                result = result.replace(token, captured, 1)
            return result

    for path in files:
        try:
            if should_exclude(path, exclude_list):
                skipped.append(path)
                log_lines.append(f"Исключён: {path}")
                continue

            with open(path, 'r', encoding='utf-8') as f:
                data = f.read()

            if use_any_mode:
                if not regex.search(data):
                    not_found.append(path)
                    log_lines.append(f"Фрагмент не найден: {path}")
                    continue
            else:
                if find_text not in data:
                    not_found.append(path)
                    log_lines.append(f"Фрагмент не найден: {path}")
                    continue

            ok, _ = make_backup(path, root, log_lines)
            if not ok:
                errors.append((path, "backup failed"))
                continue

            if use_any_mode:
                new_data = regex.sub(replacement_callback, data)
            else:
                new_data = data.replace(find_text, replace_text)

            with open(path, 'w', encoding='utf-8') as f:
                f.write(new_data)

            processed += 1
            log_lines.append(f"Обработан: {path}")

        except Exception as e:
            errors.append((path, str(e)))
            log_lines.append(f"Ошибка при обработке {path}: {e}")

    log_lines.append("=== РЕЗЮМЕ ===")
    if errors:
        for p, e in errors:
            log_lines.append(f"Ошибка: {p} -> {e}")
    if skipped:
        log_lines.append(f"Исключено файлов: {len(skipped)}")
    if not_found:
        log_lines.append(f"Файлов без вхождений: {len(not_found)}")
    log_lines.append(f"Обработано: {processed} из {total}")

    return {"total": total, "processed": processed, "skipped": skipped, "not_found": not_found, "errors": errors}


def show_logs(log_lines):
    print()
    print(f"{ACID}{BOLD}=== РЕЗУЛЬТАТ ==={RESET}")
    print()
    for ln in log_lines:
        if ln.startswith(("Ошибка", "Не удалось", "Критическая ошибка", "ОШИБКА ШАБЛОНА")):
            print(f"{RED}{ln}{RESET}")
        elif ln.startswith(("Обработан", "Бэкап", "Найдено", "===", "Режим <any>")):
            print(f"{LIME}{ln}{RESET}")
        else:
            print(f"{ACID}{ln}{RESET}")


def main():
    root = os.getcwd()
    result = None

    if CURSES_AVAILABLE:
        try:
            result = curses.wrapper(split_ui)
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            print(f"{LIME}Разделённый режим недоступен: {exc}{RESET}")
            print(f"{DIM}Использую последовательный режим ввода.{RESET}")
            result = fallback_ui()
    else:
        result = fallback_ui()

    if result is None:
        return

    find_text, replace_text = result
    exclude_list = prompt_exclusions()

    log_lines = []
    try:
        process_replacements(root, find_text, replace_text, exclude_list, log_lines)
    except Exception as e:
        log_lines.append(f"Критическая ошибка: {e}")

    show_logs(log_lines)


if __name__ == "__main__":
    try:
        locale.setlocale(locale.LC_ALL, "")
    except Exception:
        pass
    try:
        main()
    except KeyboardInterrupt:
        print()
        print(f"{LIME}Прервано пользователем.{RESET}")
