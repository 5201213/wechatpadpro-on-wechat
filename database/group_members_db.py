import sqlite3
import os
from common.log import logger

DB_PATH = os.path.join(os.path.dirname(__file__), "group_members.db")

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    # 创建群成员表
    c.execute('''
        CREATE TABLE IF NOT EXISTS group_members (
            group_id TEXT,
            wxid TEXT,
            display_name TEXT,
            nickname TEXT,
            PRIMARY KEY (group_id, wxid)
        )
    ''')
    
    # 添加群名称字段到现有表（如果不存在）
    try:
        c.execute('ALTER TABLE group_members ADD COLUMN group_name TEXT')
    except:
        pass  # 字段已存在
    
    conn.commit()
    conn.close()

def save_group_members_to_db(group_id, members):
    init_db()
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    for member in members:
        # 修正字段名：实际API返回的是小写字段名
        user_name = member.get("user_name") or member.get("UserName") or member.get("wxid")
        nick_name = member.get("nick_name") or member.get("NickName") or member.get("nickname")
        display_name = member.get("display_name") or member.get("DisplayName")
        
        c.execute('''
            INSERT OR REPLACE INTO group_members (group_id, wxid, display_name, nickname)
            VALUES (?, ?, ?, ?)
        ''', (
            group_id,
            user_name,
            display_name,
            nick_name,
        ))
    conn.commit()
    conn.close()

def get_group_member_from_db(group_id, wxid):
    init_db()
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        SELECT display_name, nickname FROM group_members WHERE group_id=? AND wxid=?
    ''', (group_id, wxid))
    row = c.fetchone()
    conn.close()
    if row:
        return {"display_name": row[0], "nickname": row[1]}
    return None 

def save_group_info(group_id, group_name):
    """保存群名称到现有表"""
    init_db()
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    # 更新该群的所有记录，添加群名称
    c.execute('''
        UPDATE group_members SET group_name = ? WHERE group_id = ?
    ''', (group_name, group_id))
    conn.commit()
    conn.close()
    logger.debug(f"[db] 保存群名称: {group_id} -> {group_name}")

def get_group_name_from_db(group_id):
    """从现有表获取群名称"""
    init_db()
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        SELECT group_name FROM group_members WHERE group_id=? AND group_name IS NOT NULL LIMIT 1
    ''', (group_id,))
    row = c.fetchone()
    conn.close()
    if row and row[0]:
        return row[0]
    return None

def get_user_nickname_from_db(wxid):
    """从群成员数据库获取用户昵称（任意一个群中的昵称）"""
    init_db()
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    # 从群成员表中查找该用户的昵称（取任意一个群中的昵称）
    c.execute('''
        SELECT nickname FROM group_members WHERE wxid=? AND nickname IS NOT NULL LIMIT 1
    ''', (wxid,))
    row = c.fetchone()
    conn.close()
    if row and row[0]:
        return row[0]
    return None