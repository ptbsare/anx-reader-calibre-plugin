#!/bin/bash

# 定义文件路径和目录
BASE_DIR="/mnt/user/volume1/电子书库/webdav/anx"
WORKSPACE_DIR="${BASE_DIR}/data"
DB_PATH="${BASE_DIR}/database7.db"
FILE_DIR="$WORKSPACE_DIR/file"
COVER_DIR="$WORKSPACE_DIR/cover"

# 确保封面目录存在
mkdir -p "$COVER_DIR"

# 允许的电子书扩展名
ALLOWED_EXTENSIONS=("epub" "mobi" "azw3" "fb2" "txt" "pdf")

# ISO 8601 时间格式函数
get_iso_time() {
    date -u +%Y-%m-%dT%H:%M:%S.%3NZ
}

echo "--- 开始处理电子书文件 ---"

# 检查数据库文件是否存在
if [ ! -f "$DB_PATH" ]; then
    echo "错误：数据库文件 $DB_PATH 不存在。请确保文件已提供。"
    exit 1
fi

# 检查tb_books表是否存在
TABLE_EXISTS=$(sqlite3 "$DB_PATH" "SELECT name FROM sqlite_master WHERE type='table' AND name='tb_books';" 2>/dev/null)
if [ -z "$TABLE_EXISTS" ]; then
    echo "错误：数据库文件 $DB_PATH 中不存在 'tb_books' 表。请确保表已创建。"
    exit 1
fi
echo "数据库文件 $DB_PATH 和表 'tb_books' 已确认存在。"

PROCESSED_COUNT=0
SKIPPED_COUNT=0

# 遍历file目录下的所有文件
for file_path in "$WORKSPACE_DIR"/*; do
    if [ -f "$file_path" ]; then
        filename=$(basename "$file_path")
        extension="${filename##*.}"
        base_name="${filename%.*}" # 例如 "Title - Author"

        # 检查文件扩展名是否在允许列表中
        is_allowed=false
        for ext in "${ALLOWED_EXTENSIONS[@]}"; do
            if [[ "$extension" == "$ext" ]]; then
                is_allowed=true
                break
            fi
        done

        if ! "$is_allowed"; then
            echo "跳过文件：$filename (不支持的格式：$extension)"
            continue
        fi

        echo "正在处理文件：$filename"

        # 尝试解析 title 和 author
        # 假设格式为 "Title - Author"
        if [[ "$base_name" =~ ^(.*)\ -\ (.*)$ ]]; then
            title="${BASH_REMATCH[1]}"
            author="${BASH_REMATCH[2]}"
        else
            echo "警告：无法从文件名 '$filename' 解析出标题和作者，使用默认值。"
            title="$base_name"
            author="未知作者"
        fi

        # 计算文件MD5
        # 兼容 md5sum (Linux) 和 md5 (macOS)
        if command -v md5sum &> /dev/null; then
            file_md5=$(md5sum "$file_path" | awk '{print $1}')
        elif command -v md5 &> /dev/null; then
            file_md5=$(md5 -q "$file_path")
        else
            echo "错误：未找到 md5sum 或 md5 命令，无法计算文件MD5。请安装其中一个。"
            exit 1
        fi

        # 检查MD5是否已存在于数据库中
        EXISTING_ID=$(sqlite3 "$DB_PATH" "SELECT id FROM tb_books WHERE file_md5 = '$file_md5';" 2>/dev/null)
        if [ -n "$EXISTING_ID" ]; then
            echo "文件 $filename (MD5: $file_md5) 已存在于数据库中，跳过。"
            SKIPPED_COUNT=$((SKIPPED_COUNT + 1))
            continue
        fi


        # 处理电子书图片
        dest_file_path="$FILE_DIR/$filename"

        if [ -f "$file_path" ]; then
            mv "$file_path" "$dest_file_path"
            echo "电子书文件已移动到：$dest_file_path"
        else
            echo "未找到文件 '$file_path"
        fi

        # 处理封面图片
        source_cover_path="${file_path%.*}.jpg" # 假设封面与电子书同名且为.jpg
        dest_cover_filename="${base_name}.jpg"
        dest_cover_path="$COVER_DIR/$dest_cover_filename"
        cover_relative_path=""

        if [ -f "$source_cover_path" ]; then
            mv "$source_cover_path" "$dest_cover_path"
            cover_relative_path="cover/$dest_cover_filename"
            echo "封面已移动到：$dest_cover_path"
        else
            echo "未找到文件 '$filename' 的同名封面图片：$source_cover_path"
        fi

        # 准备数据库路径
        file_relative_path="file/$filename"

        current_time=$(get_iso_time)

        # 插入或更新数据库
        # 注意：这里直接使用 INSERT，因为前面已经通过 MD5 检查了重复
        SQL_INSERT="INSERT INTO tb_books (title, cover_path, file_path, author, create_time, update_time, file_md5, last_read_position, reading_percentage, is_deleted, rating, group_id, description) VALUES ('$title', '$cover_relative_path', '$file_relative_path', '$author', '$current_time', '$current_time', '$file_md5', '', 0.0, 0, 0.0, 0, '');"

        sqlite3 "$DB_PATH" "$SQL_INSERT" 2>/dev/null
        if [ $? -eq 0 ]; then
            echo "文件 $filename 的信息已成功插入数据库。"
            PROCESSED_COUNT=$((PROCESSED_COUNT + 1))
        else
            echo "错误：插入文件 $filename 信息到数据库失败。"
        fi
    fi
done

echo "--- 电子书处理完成 ---"
echo "成功处理并插入数据库的文件数量：$PROCESSED_COUNT"
echo "因已存在而跳过的文件数量：$SKIPPED_COUNT"
echo "-----------------------"
