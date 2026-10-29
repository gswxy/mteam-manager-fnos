#!/usr/bin/env bash
# 馒头管家 FPK 构建脚本
# 用法: scripts/build_fpk.sh [x86|arm] [版本号]
# 依赖: curl、tar、python3(+pip)、node/npm（构建前端）
# 产物: dist-fpk/mteam-manager_<版本>_<arch>.fpk 与 sha256
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ARCH="${1:-x86}"
PYBIN="$(command -v python3 || command -v python)"
VERSION="${2:-$(cd "$ROOT" && "$PYBIN" -c 'import re;print(re.search(r"__version__ = .([0-9.]+).", open("backend/app/__init__.py",encoding="utf-8").read()).group(1))')}"
OUT="${ROOT}/dist-fpk"
STAGE="${OUT}/fpk-stage"

# python-build-standalone 版本（install_only 已裁剪）
PY_RELEASE="20261026"
PY_NAME="cpython-3.11.18+20261026"
case "$ARCH" in
x86)  PS_ARCH="x86_64-unknown-linux-gnu";  PLATFORM="x86";  MANY="manylinux2014_x86_64" ;;
arm)  PS_ARCH="aarch64-unknown-linux-gnu"; PLATFORM="arm";  MANY="manylinux2014_aarch64" ;;
*) echo "unsupported arch: $ARCH (x86|arm)"; exit 1 ;;
esac
PY_TARBALL="${PY_NAME}-${PS_ARCH}-install_only.tar.gz"
PY_URL="https://github.com/astral-sh/python-build-standalone/releases/download/${PY_RELEASE}/${PY_TARBALL}"

echo "==> 版本 $VERSION / 架构 $ARCH"
rm -rf "$STAGE"; mkdir -p "$STAGE" "$OUT"

# 1) FPK 骨架
cp -r "$ROOT/fpk/." "$STAGE/"
sed -i "s/^platform.*=.*/platform              = ${PLATFORM}/" "$STAGE/manifest"
chmod +x "$STAGE"/cmd/*

# 2) 图标（已作为仓库资产提交；缺失时才需要 Pillow 重新生成）
if [ ! -f "$STAGE/ICON.PNG" ]; then
    "$PYBIN" "$ROOT/scripts/gen_icons.py" "$STAGE"
fi

# 3) 前端
if [ ! -d "$ROOT/frontend/dist" ] || [ -z "$(ls -A "$ROOT/frontend/dist" 2>/dev/null)" ]; then
    echo "==> 构建前端"
    (cd "$ROOT/frontend" && npm ci --no-fund --no-audit && npm run build)
fi
rm -rf "$STAGE/app/frontend-dist"
cp -r "$ROOT/frontend/dist" "$STAGE/app/frontend-dist"

# 4) 后端代码（不含测试与缓存）
rm -rf "$STAGE/app/backend"
mkdir -p "$STAGE/app/backend"
cp -r "$ROOT/backend/app" "$STAGE/app/backend/app"
cp "$ROOT/backend/requirements.txt" "$STAGE/app/backend/"
find "$STAGE/app/backend" -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true

# 5) Python 运行时（linux 目标机独立运行时，不依赖系统 Python）
RUNTIME="$STAGE/app/runtime"
rm -rf "$RUNTIME"
mkdir -p "$RUNTIME" "$OUT/downloads"
PY_CACHE="$OUT/downloads/$PY_TARBALL"
if [ ! -f "$PY_CACHE" ]; then
    echo "==> 下载 Python 运行时 $PY_URL"
    curl -fL --retry 3 -o "$PY_CACHE" "$PY_URL"
fi
tar -xzf "$PY_CACHE" -C "$RUNTIME" --strip-components=1

# 6) 依赖安装（交叉目标平台 wheel；pip 自动解析完整依赖树）
SITE="$RUNTIME/lib/python3.11/site-packages"
"$PYBIN" -m pip install --quiet --disable-pip-version-check \
    --platform "$MANY" --only-binary=:all: \
    --target "$SITE" -r "$ROOT/backend/requirements.txt"

# 运行时瘦身
rm -rf "$SITE/pip" "$SITE/setuptools"* "$SITE/_distutils_hack" "$SITE/pkg_resources" \
       "$RUNTIME/lib/python3.11/idlelib" "$RUNTIME/lib/python3.11/lib2to3" \
       "$RUNTIME/lib/python3.11/tkinter" "$RUNTIME/lib/python3.11/ensurepip" \
       "$RUNTIME/lib/python3.11/test" 2>/dev/null || true
find "$RUNTIME" -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true
find "$RUNTIME" -name '*.pyc' -delete 2>/dev/null || true
chmod +x "$RUNTIME/bin/"* 2>/dev/null || true

# 7) 打包（FPK = tar.gz，与 fnpack 产物一致；官方 fnpack 可用时优先）
FPK="$OUT/mteam-manager_${VERSION}_${ARCH}.fpk"
if command -v fnpack >/dev/null 2>&1; then
    echo "==> 使用 fnpack 打包"
    (cd "$STAGE" && fnpack build --directory "$STAGE" && mv ./*.fpk "$FPK")
else
    echo "==> 使用 tar.gz 打包（与 FPK 规范一致）"
    (cd "$STAGE" && tar -czf "$FPK" manifest ICON.PNG ICON_256.PNG app config cmd wizard)
fi

# 8) 校验
cd "$OUT"
sha256sum "$(basename "$FPK")" > "$(basename "$FPK").sha256"
echo "==> 完成: $FPK ($(du -h "$FPK" | cut -f1))"
cat "$(basename "$FPK").sha256"
