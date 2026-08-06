
# projects_manager.py - 多项目管理服务
import json
import uuid
import base64
import mimetypes
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional, Any
from oohstory_library.services.genre_catalog import canonical_genre_id, canonical_substyle_id
from oohstory_library.services.tone_catalog import TONE_TAG_CATALOG

from oohstory_library.services.project_prompt_store import ensure_project_prompts

# 全局配置目录
GLOBAL_CONFIG_DIR = Path.home() / ".webnovel"
PROJECTS_FILE = GLOBAL_CONFIG_DIR / "projects.json"
APP_ROOT = Path(__file__).resolve().parents[3]


def normalize_tone_tags(tags: Optional[List[str]]) -> List[str]:
    """清洗作品基调；允许保存全局书库索引未来新增的标签。"""
    normalized: List[str] = []
    seen = set()
    for value in tags or []:
        if not isinstance(value, str):
            continue
        tag = value.strip()
        if not tag or len(tag) > 24 or tag in seen:
            continue
        normalized.append(tag)
        seen.add(tag)
        if len(normalized) >= 20:
            break
    return normalized


def get_default_projects_root() -> Path:
    return (APP_ROOT.parent / "webnovel-projects").resolve()


def save_project_cover(project_path: str, cover_data: str) -> str:
    project_root = Path(project_path).expanduser().resolve()
    meta_dir = project_root / ".webnovel"
    meta_dir.mkdir(parents=True, exist_ok=True)

    header, _, payload = cover_data.partition(",")
    ext = ".png"
    if header.startswith("data:"):
        mime = header[5:].split(";", 1)[0]
        guessed = mimetypes.guess_extension(mime or "")
        if guessed:
            ext = ".jpg" if guessed == ".jpe" else guessed

    cover_path = meta_dir / f"cover{ext}"
    for old in meta_dir.glob("cover.*"):
        if old != cover_path:
            try:
                old.unlink()
            except Exception:
                pass

    raw = base64.b64decode(payload if payload else cover_data)
    cover_path.write_bytes(raw)
    return str(cover_path)


def get_project_cover_url(project_id: str, project_path: str) -> Optional[str]:
    project_root = Path(project_path).expanduser().resolve()
    meta_dir = project_root / ".webnovel"
    for file in sorted(meta_dir.glob("cover.*")):
        return f"/api/projects/{project_id}/cover-file"
    return None

def _ensure_config_dir():
    """确保全局配置目录存在"""
    GLOBAL_CONFIG_DIR.mkdir(parents=True, exist_ok=True)

def _load_projects_data() -> Dict[str, Any]:
    """加载项目列表数据"""
    _ensure_config_dir()
    data = {"current_project": None, "projects": []}

    if PROJECTS_FILE.exists():
        try:
            data = json.loads(PROJECTS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass

    # 自动发现根目录项目 (兼容旧版本) - 仅当没有任何项目时
    if not data["projects"]:
        # 注意：此处不应盲目将根目录设为项目，除非它确实包含正文/大纲目录
        root_path = Path(__file__).parent.parent.parent
        if (root_path / "大纲").exists() or (root_path / "正文").exists():
            state_file = root_path / ".webnovel" / "state.json"
            # ... (rest of the discovery logic remains similar but limited)
            try:
                if state_file.exists():
                    state = json.loads(state_file.read_text(encoding="utf-8"))
                    name = state.get("project_info", {}).get("title", state.get("title", "默认项目"))
                else:
                    name = "默认项目"
            except Exception:
                name = "默认项目"

            default_project = {
                "id": str(uuid.uuid4()),
                "name": name,
                "path": str(root_path.absolute()),
                "genre": "未知",
                "created_at": datetime.now().strftime("%Y-%m-%d"),
                "last_opened": datetime.now().strftime("%Y-%m-%d"),
                "exists": True
            }
            data["projects"].append(default_project)
            if not data["current_project"]:
                data["current_project"] = default_project["path"]
            _save_projects_data(data)

    return data

def find_project_by_path(path: Path) -> Optional[Dict[str, Any]]:
    """根据路径回溯查找所属项目"""
    data = _load_projects_data()
    path = path.expanduser().resolve()

    # 尝试匹配路径或其父目录
    best_match = None
    max_len = -1

    for p in data.get("projects", []):
        p_path = Path(p["path"]).expanduser().resolve()
        try:
            if path == p_path or p_path in path.parents:
                # 选取最长匹配路径（最具体的子项目）
                if len(str(p_path)) > max_len:
                    max_len = len(str(p_path))
                    best_match = p
        except ValueError:
            continue

    return best_match

def _save_projects_data(data: Dict[str, Any]):
    """保存项目列表数据"""
    _ensure_config_dir()
    PROJECTS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

def _try_relocate_project(project: Dict[str, Any]) -> Optional[str]:
    """当项目路径失效时，在 home 目录下搜索 webnovel-projects 目录并自动重定位。

    搜索优先级：
      1. ~/webnovel-projects/<项目目录名>
      2. ~/<子目录>/webnovel-projects/<项目目录名>
      3. ~/<子目录>/workspace/webnovel-projects/<项目目录名>

    找到后验证目录含有 .webnovel / 大纲 / 正文 之一才视为有效项目。
    """
    project_dirname = Path(project["path"]).name
    if not project_dirname:
        return None

    home = Path.home()
    candidates: List[Path] = []

    # 优先级 1：~/webnovel-projects/<name>
    c0 = home / "webnovel-projects" / project_dirname
    if c0.is_dir():
        candidates.append(c0)

    # 优先级 2 & 3：遍历 home 下直接子目录（含隐藏目录，限深度避免耗时）
    try:
        for level1 in sorted(home.iterdir()):
            if not level1.is_dir():
                continue
            # ~/<dir>/webnovel-projects/<name>
            c1 = level1 / "webnovel-projects" / project_dirname
            if c1.is_dir():
                candidates.append(c1)
            # ~/<dir>/workspace/webnovel-projects/<name>
            c2 = level1 / "workspace" / "webnovel-projects" / project_dirname
            if c2.is_dir():
                candidates.append(c2)
    except PermissionError:
        pass

    # 选取第一个包含有效项目标志的候选路径
    for candidate in candidates:
        if any((candidate / marker).exists() for marker in (".webnovel", "大纲", "正文")):
            return str(candidate.resolve())

    return None


def list_projects() -> List[Dict[str, Any]]:
    """获取所有项目列表（并同步最新标题、章节数、字数）"""
    data = _load_projects_data()
    projects = data.get("projects", [])
    updated = False

    # 检查项目路径是否存在，路径失效时尝试自动重定位
    for p in projects:
        p_path = Path(p["path"])
        p["exists"] = p_path.exists()

        if not p["exists"]:
            relocated = _try_relocate_project(p)
            if relocated:
                old_path = p["path"]
                p["path"] = relocated
                p["exists"] = True
                updated = True
                # 同步更新 current_project 引用
                if data.get("current_project") == old_path:
                    data["current_project"] = relocated
                print(f"[项目重定位] {p['name']}: {old_path} -> {relocated}")

        if p["exists"]:
            # 尝试读取最新的 state.json
            state_file = p_path / ".webnovel" / "state.json"
            if state_file.exists():
                try:
                    state = json.loads(state_file.read_text(encoding="utf-8"))
                    # 获取最新标题
                    new_title = ""
                    new_genre = ""
                    if "project_info" in state:
                        project_info = state["project_info"]
                        new_title = project_info.get("title", "")
                        new_genre = project_info.get("genre", "")
                        p["substyle"] = project_info.get("substyle", p.get("substyle", ""))
                        p["tone_tags"] = normalize_tone_tags(
                            project_info.get("tone_tags") or []
                        )
                    else:
                        new_title = state.get("title", "")
                        new_genre = state.get("genre", "")
                        p["substyle"] = state.get("substyle", p.get("substyle", ""))
                        p["tone_tags"] = normalize_tone_tags(
                            state.get("tone_tags") or []
                        )

                    # 如果标题不一致，更新缓存
                    if new_title and new_title != p["name"]:
                        p["name"] = new_title
                        updated = True
                    if new_genre and new_genre != p.get("genre"):
                        p["genre"] = new_genre
                        updated = True
                except Exception:
                    pass

            # 统计章节数和总字数（支持卷目录递归）
            chapters_dir = p_path / "正文"
            if chapters_dir.exists():
                try:
                    from oohstory_library.services.bilingual_service import is_lang_file
                    chapter_files = []
                    for f in chapters_dir.rglob("第*章*.md"):
                        rel_parts = f.relative_to(chapters_dir).parts[:-1]
                        if any(part.startswith('.') for part in rel_parts):
                            continue
                        if not is_lang_file(f, "zh"):
                            continue
                        chapter_files.append(f)
                    p["total_chapters"] = len(chapter_files)
                    total_words = 0
                    for f in chapter_files:
                        try:
                            total_words += len(f.read_text(encoding="utf-8"))
                        except Exception:
                            pass
                    p["total_words"] = total_words
                except Exception:
                    p["total_chapters"] = 0
                    p["total_words"] = 0
            else:
                p["total_chapters"] = 0
                p["total_words"] = 0
        else:
            p["total_chapters"] = 0
            p["total_words"] = 0

        legacy_cover = p.get("cover_data")
        if legacy_cover and p.get("exists"):
            try:
                save_project_cover(p["path"], legacy_cover)
                p.pop("cover_data", None)
                updated = True
            except Exception:
                pass
        p["cover_url"] = get_project_cover_url(p.get("id", ""), p["path"]) if p.get("exists") else None

    # 如果有更新，保存回 projects.json
    if updated:
        _save_projects_data(data)

    return projects

def get_current_project() -> Optional[Dict[str, Any]]:
    """获取当前项目"""
    data = _load_projects_data()
    current_path = data.get("current_project")
    if not current_path:
        return None
    for p in data.get("projects", []):
        if p["path"] == current_path:
            p["exists"] = Path(p["path"]).exists()
            return p
    return None

def set_current_project(path: Path):
    """设置当前项目路径"""
    data = _load_projects_data()
    abs_path = str(path.expanduser().resolve())

    # 确保保存为绝对路径
    data["current_project"] = abs_path

    # 更新最后打开时间
    for p in data["projects"]:
        if str(Path(p["path"]).expanduser().resolve()) == abs_path:
            p["last_opened"] = datetime.now().strftime("%Y-%m-%d")
            break

    _save_projects_data(data)


def get_current_project_path() -> Optional[Path]:
    """获取当前项目路径"""
    project = get_current_project()
    if project and project.get("exists"):
        return Path(project["path"])
    return None

def create_project(
    name: str,
    path: str = "",
    genre: str = "玄幻",
    substyle: str = "",
    logline: str = "",
    target_words: int = 1000000,
    target_chapters: int = 300,
    chapter_word_target: int = 3000,
    init_mode: str = "outline_and_settings",
    audience_mode: str = "通用",
    tone: str = "热血",
    tone_tags: Optional[List[str]] = None,
    narrative_view: str = "第三人称",
    planned_volumes: int = 6,
) -> Dict[str, Any]:
    """新建项目"""
    safe_name = (name or "").strip()
    if not safe_name:
        return {"error": "项目名称不能为空"}

    if not path:
        path = str((get_default_projects_root() / safe_name).resolve())

    project_path = Path(path).expanduser().resolve()
    genre = canonical_genre_id(genre) or "玄幻"
    substyle = canonical_substyle_id(genre, substyle)
    target_words = max(int(target_words or 0), 10000)
    target_chapters = max(int(target_chapters or 0), 1)
    chapter_word_target = max(int(chapter_word_target or 0), 500)
    planned_volumes = max(int(planned_volumes or 0), 1)
    normalized_tone_tags = normalize_tone_tags(tone_tags)

    # 先查重，再操作文件系统
    data = _load_projects_data()
    for p in data["projects"]:
        if Path(p["path"]).expanduser().resolve() == project_path:
            return {"error": "项目路径已存在", "project": p}

    # 安全：后续操作文件系统
    project_path.mkdir(parents=True, exist_ok=True)

    # 创建项目基础目录结构
    (project_path / "大纲").mkdir(exist_ok=True)
    (project_path / "正文").mkdir(exist_ok=True)
    (project_path / "设定集").mkdir(exist_ok=True)
    (project_path / ".webnovel").mkdir(exist_ok=True)

    # 初始化 state.json
    state_file = project_path / ".webnovel" / "state.json"
    is_manual_project = init_mode == "manual_only"
    state = {
        "title": safe_name,
        "genre": genre,
        "substyle": substyle,
        "created_at": datetime.now().isoformat(),
        "initialized": is_manual_project,
        "current_chapter": 0,
        "project_info": {
            "title": safe_name,
            "genre": genre,
            "substyle": substyle,
            "logline": (logline or '').strip(),
            "target_words": target_words,
            "target_chapters": target_chapters,
            "chapter_word_target": chapter_word_target,
            "init_mode": init_mode,
            "audience_mode": audience_mode,
            "tone": tone,
            "tone_tags": normalized_tone_tags,
            "narrative_view": narrative_view,
            "planned_volumes": planned_volumes,
            "status": "筹备中"
        }
    }
    state_file.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    ensure_project_prompts(project_path, genre, substyle)

    settings_dir = project_path / '设定集'
    outline_dir = project_path / '大纲'
    world_file = settings_dir / '世界观.md'
    power_file = settings_dir / '力量体系.md'
    protagonist_file = settings_dir / '主角卡.md'
    golden_finger_file = settings_dir / '金手指设计.md'
    outline_file = outline_dir / '总纲.md'

    if not world_file.exists():
        world_file.write_text(f"# 世界观\n\n{(logline or '').strip()}\n", encoding='utf-8')
    if is_manual_project:
        if not outline_file.exists():
            outline_file.write_text(f"# 总纲\n\n## 故事核心\n{(logline or '').strip() or '待补充'}\n\n## 主线推进\n- 待补充\n", encoding='utf-8')
        if not protagonist_file.exists():
            protagonist_file.write_text("# 主角卡\n\n## 基本信息\n- **姓名**：待填写\n- **身份**：待填写\n- **角色相框**：待填写\n- **当前状态**：待填写\n- **当前地点**：待填写\n- **最后更新章节**：第0章 / 待补充\n\n## 核心设定\n- **核心能力/金手指**：待填写\n- **当前目标**：待填写\n- **主要矛盾**：待填写\n", encoding='utf-8')
        if not power_file.exists():
            power_file.write_text("# 力量体系\n\n## 基础规则\n待填写\n\n## 境界/等级\n待填写\n", encoding='utf-8')
        if not golden_finger_file.exists():
            golden_finger_file.write_text("# 金手指设计\n\n## 名称\n待填写\n\n## 类型\n待填写\n\n## 核心机制\n待填写\n\n## 成长阶段\n待填写\n\n## 代价与限制\n待填写\n", encoding='utf-8')

    # 添加到项目列表（复用已加载的 data）
    project = {
        "id": str(uuid.uuid4()),
        "name": safe_name,
        "path": str(project_path),
        "genre": genre,
        "substyle": substyle,
        "tone_tags": normalized_tone_tags,
        "created_at": datetime.now().strftime("%Y-%m-%d"),
        "last_opened": datetime.now().strftime("%Y-%m-%d")
    }

    data["projects"].append(project)
    data["current_project"] = project["path"]
    _save_projects_data(data)

    return {"success": True, "project": project}

def switch_project(project_id: str) -> Dict[str, Any]:
    """切换当前项目"""
    data = _load_projects_data()
    for p in data["projects"]:
        if p["id"] == project_id:
            if not Path(p["path"]).expanduser().exists():
                return {"error": "项目路径不存在", "path": p["path"]}
            data["current_project"] = p["path"]
            p["last_opened"] = datetime.now().strftime("%Y-%m-%d")
            _save_projects_data(data)
            return {"success": True, "project": p}
    return {"error": "项目不存在"}

def rename_project(project_id: str, new_name: str) -> Dict[str, Any]:
    """重命名项目（显示名 + 项目目录名）"""
    safe_name = (new_name or '').strip()
    if not safe_name:
        return {"error": "项目名称不能为空"}

    data = _load_projects_data()
    current_project_path = data.get("current_project")

    for p in data["projects"]:
        if p["id"] != project_id:
            continue

        project_path = Path(p["path"]).expanduser().resolve()
        if not project_path.exists():
            return {"error": "项目路径不存在", "path": str(project_path)}

        new_project_path = project_path.parent / safe_name
        if new_project_path != project_path:
            if new_project_path.exists():
                return {"error": f"目标目录已存在: {new_project_path}"}
            project_path.rename(new_project_path)
            project_path = new_project_path

        state_file = project_path / ".webnovel" / "state.json"
        if state_file.exists():
            try:
                state = json.loads(state_file.read_text(encoding="utf-8"))
            except Exception:
                state = {}
        else:
            state = {}

        if "project_info" not in state or not isinstance(state.get("project_info"), dict):
            state["project_info"] = {
                "title": state.get("title", p.get("name", safe_name)),
                "genre": state.get("genre", p.get("genre", "")),
                "substyle": state.get("substyle", p.get("substyle", "")),
                "description": state.get("description", "")
            }

        state["title"] = safe_name
        state["project_info"]["title"] = safe_name
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

        old_path = p["path"]
        p["name"] = safe_name
        p["path"] = str(project_path)
        p["updated_at"] = datetime.now().isoformat()
        if current_project_path == old_path:
            data["current_project"] = p["path"]
        _save_projects_data(data)
        return {"success": True, "project": p, "old_path": old_path, "new_path": p["path"]}

    return {"error": "项目不存在"}


def import_project(path: str) -> Dict[str, Any]:
    """导入现有项目"""
    project_path = Path(path).expanduser().resolve()
    if not project_path.exists():
        return {"error": "路径不存在"}

    # 尝试读取项目信息
    state_file = project_path / ".webnovel" / "state.json"
    name = project_path.name
    genre = "未知"

    if state_file.exists():
        try:
            state = json.loads(state_file.read_text(encoding="utf-8"))
            substyle = ""
            if "project_info" in state:
                name = state.get("project_info", {}).get("title", state.get("title", name))
                genre = state.get("project_info", {}).get("genre", state.get("genre", genre))
                substyle = state.get("project_info", {}).get("substyle", state.get("substyle", ""))
            else:
                name = state.get("title", name)
                genre = state.get("genre", genre)
                substyle = state.get("substyle", "")
            ensure_project_prompts(project_path, genre, substyle)
        except Exception:
            pass

    data = _load_projects_data()

    # 检查是否已存在
    for p in data["projects"]:
        if p["path"] == str(project_path.absolute()):
            data["current_project"] = p["path"]
            _save_projects_data(data)
            return {"success": True, "project": p, "already_exists": True}

    # 新增
    project = {
        "id": str(uuid.uuid4()),
        "name": name,
        "path": str(project_path.absolute()),
        "genre": genre,
        "created_at": datetime.now().strftime("%Y-%m-%d"),
        "last_opened": datetime.now().strftime("%Y-%m-%d")
    }
    data["projects"].append(project)
    data["current_project"] = project["path"]
    _save_projects_data(data)

    return {"success": True, "project": project}

def delete_project(project_id: str, delete_files: bool = False) -> Dict[str, Any]:
    """删除项目（从索引移除，可选删除文件）"""
    data = _load_projects_data()
    for i, p in enumerate(data["projects"]):
        if p["id"] == project_id:
            removed = data["projects"].pop(i)
            if data["current_project"] == removed["path"]:
                data["current_project"] = data["projects"][0]["path"] if data["projects"] else None
            _save_projects_data(data)

            if delete_files:
                import shutil
                try:
                    shutil.rmtree(removed["path"])
                except Exception as e:
                    return {"success": True, "warning": f"文件删除失败: {e}"}

            return {"success": True}
    return {"error": "项目不存在"}
