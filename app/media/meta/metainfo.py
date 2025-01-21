import os.path
import regex as re

import log
from app.helper import WordsHelper
from app.media.meta.metaanime import MetaAnime
from app.media.meta.metavideo import MetaVideo
from app.utils.types import MediaType
from config import RMT_MEDIAEXT, Config


def MetaInfo(title, subtitle=None, mtype=None):
    """
    媒体整理入口，根据名称和副标题，判断是哪种类型的识别，返回对应对象
    :param title: 标题、种子名、文件名
    :param subtitle: 副标题、描述
    :param mtype: 指定识别类型，为空则自动识别类型
    :return: MetaAnime、MetaVideo
    """

    # 应用自定义识别词
    title, msg, used_info = WordsHelper().process(title)
    if subtitle:
        subtitle, _, _ = WordsHelper().process(subtitle)

    if msg:
        for msg_item in msg:
            log.warn("【Meta】%s" % msg_item)

    # 判断是否处理文件
    if title and os.path.splitext(title)[-1] in RMT_MEDIAEXT:
        fileflag = True
    else:
        fileflag = False

    if mtype == MediaType.ANIME or is_anime(title):
        meta_info = MetaAnime(title, subtitle, fileflag)
    else:
        meta_info = MetaVideo(title, subtitle, fileflag)

    id_str = __get_id(title)
    if id_str:
        meta_info.adult = True
        meta_info.type = MediaType.MOVIE

    meta_info.ignored_words = used_info.get("ignored")
    meta_info.replaced_words = used_info.get("replaced")
    meta_info.offset_words = used_info.get("offset")

    return meta_info

def is_anime(name):
    """
    判断是否为动漫
    :param name: 名称
    :return: 是否动漫
    """
    if not name:
        return False
    if re.search(r'【[+0-9XVPI-]+】\s*【', name, re.IGNORECASE):
        return True
    if re.search(r'\s+-\s+[\dv]{1,4}\s+', name, re.IGNORECASE):
        return True
    if re.search(r"S\d{2}\s*-\s*S\d{2}|S\d{2}|\s+S\d{1,2}|EP?\d{2,4}\s*-\s*EP?\d{2,4}|EP?\d{2,4}|\s+EP?\d{1,4}", name,
                 re.IGNORECASE):
        return False
    if re.search(r'\[[+0-9XVPI-]+]\s*\[', name, re.IGNORECASE):
        return True
    return False

def __get_id(title_str: str) -> str:
    """从给定的标题中提取番号（DVD ID）"""
    ignored_id_pattern = Config().get_config('app').get('ignored_id_pattern', [])
    ignore_pattern = re.compile('|'.join(ignored_id_pattern))
    norm = ignore_pattern.sub('', title_str).upper()
    if 'FC2' in norm:
        # 根据FC2 Club的影片数据，FC2编号为5-7个数字
        match = re.search(r'FC2[^A-Z\d]{0,5}(PPV[^A-Z\d]{0,5})?(\d{5,7})', norm, re.I)
        if match:
            return 'FC2-' + match.group(2)
    elif 'HEYDOUGA' in norm:
        match = re.search(r'(HEYDOUGA)[-_]*(\d{4})[-_]0?(\d{3,5})', norm, re.I)
        if match:
            return '-'.join(match.groups())
    elif 'GETCHU' in norm:
        match = re.search(r'GETCHU[-_]*(\d+)', norm, re.I)
        if match:
            return 'GETCHU-' + match.group(1)
    elif 'GYUTTO' in norm:
        match = re.search(r'GYUTTO-(\d+)', norm, re.I)
        if match:
            return 'GYUTTO-' + match.group(1)
    elif '259LUXU' in norm: # special case having form of '259luxu'
        match = re.search(r'259LUXU-(\d+)', norm, re.I)
        if match:
            return '259LUXU-' + match.group(1)

    else:
        # 先尝试移除可疑域名进行匹配，如果匹配不到再使用原始文件名进行匹配
        no_domain = re.sub(r'\w{3,10}\.(COM|NET|APP|XYZ)', '', norm, flags=re.I)
        if no_domain != norm:
            avid = __get_id(no_domain)
            if avid:
                return avid
        # 匹配缩写成hey的heydouga影片。由于番号分三部分，要先于后面分两部分的进行匹配
        match = re.search(r'(?:HEY)[-_]*(\d{4})[-_]0?(\d{3,5})', norm, re.I)
        if match:
            return 'heydouga-' + '-'.join(match.groups())
        # 匹配片商 MUGEN 的奇怪番号。由于MK3D2DBD的模式，要放在普通番号模式之前进行匹配
        match = re.search(r'(MKB?D)[-_]*(S\d{2,3})|(MK3D2DBD|S2M|S2MBD)[-_]*(\d{2,3})', norm, re.I)
        if match:
            if match.group(1) is not None:
                avid = match.group(1) + '-' + match.group(2)
            else:
                avid = match.group(3) + '-' + match.group(4)
            return avid
        # 匹配IBW这样带有后缀z的番号
        match = re.search(r'(IBW)[-_](\d{2,5}z)', norm, re.I)
        if match:
            return match.group(1) + '-' + match.group(2)
        # 普通番号，优先尝试匹配带分隔符的（如ABC-123）
        match = re.search(r'([A-Z]{2,10})[-_](\d{2,5})', norm, re.I)
        if match:
            return match.group(1) + '-' + match.group(2)
        # 普通番号，运行到这里时表明无法匹配到带分隔符的番号
        # 先尝试匹配东热的red, sky, ex三个不带-分隔符的系列
        # （这三个系列已停止更新，因此根据其作品编号将数字范围限制得小一些以降低误匹配概率）
        match = re.search(r'(RED[01]\d\d|SKY[0-3]\d\d|EX00[01]\d)', norm, re.I)
        if match:
            return match.group(1)
        # 然后再将影片视作缺失了-分隔符来匹配
        match = re.search(r'([A-Z]{2,})(\d{2,5})', norm, re.I)
        if match:
            return match.group(1) + '-' + match.group(2)
    # 尝试匹配TMA制作的影片（如'T28-557'，他家的番号很乱）
    match = re.search(r'(T[23]8[-_]\d{3})', norm)
    if match:
        return match.group(1)
    # 尝试匹配东热n, k系列
    match = re.search(r'(N\d{4}|K\d{4})', norm, re.I)
    if match:
        return match.group(1)
    # 尝试匹配R18-XXX的番号
    match = re.search(r'R18-?\d{3}', norm, re.I)
    if match:
        return match.group(1)
    # 尝试匹配纯数字番号（无码影片）
    match = re.search(r'(\d{6}[-_]\d{2,3})', norm)
    if match:
        return match.group(1)
    # 如果还是匹配不了，尝试将' '替换为'-'后再试，少部分影片的番号是由' '分隔的
    if ' ' in norm:
        avid1 = __get_id(norm.replace(' ', '-'))
        if avid1:
            return avid1
    # 如果还是匹配不了，尝试将')('替换为'-'后再试，少部分影片的番号是由')('分隔的
    if ')(' in norm:
        avid2 = __get_id(norm.replace(')(', '-'))
        if avid2:
            return avid2
    return ''