import base64
import os
import shutil
from time import sleep
import json
import re
from typing import Tuple, List, Optional
from urllib.parse import urlparse

from config import Config, RMT_SUBEXT
import log as logger
from app.utils.types import MediaType, SearchType
from app.helper import SiteHelper
from app.utils import RequestUtils, StringUtils, PathUtils, ExceptionUtils


class MTorrentSpider:
    """
    mTorrent API
    """
    _indexerid = None
    _domain = None
    _url = None
    _name = ""
    _proxy = None
    _cookie = None
    _ua = None
    _size = 50
    _searchurl = "https://api.%s/api/torrent/search"
    _downloadurl = "https://api.%s/api/torrent/genDlToken"
    _subtitle_list_url = "https://api.%s/api/subtitle/list"
    _subtitle_genlink_url = "https://api.%s/api/subtitle/genlink"
    _subtitle_download_url ="https://api.%s/api/subtitle/dlV2?credential=%s"
    _pageurl = "%sdetail/%s"
    _timeout = 15

    # 电影分类
    _movie_category = ['401', '419', '420', '421', '439', '405', '404']
    _tv_category = ['403', '402', '435', '438', '404', '405']
    _ninekg_category = ["410","429","426","437","431","432","425"]

    # API KEY
    _apikey = None
    # JWT Token
    _token = None

    # 标签
    _labels = {
        "0": "",
        "1": "DIY",
        "2": "国配",
        "3": "DIY 国配",
        "4": "中字",
        "5": "DIY 中字",
        "6": "国配 中字",
        "7": "DIY 国配 中字"
    }

    def __init__(self, indexer: dict):
        if indexer:
            self._indexerid = indexer.id
            self._url = indexer.domain
            self._domain = ".".join(StringUtils.get_url_domain(self._url).split(".")[-2:])
            self._searchurl = self._searchurl % self._domain
            self._name = indexer.name
            if indexer.proxy:
                self._proxy = Config().get_proxies()
            self._cookie = indexer.cookie
            self._ua = indexer.ua
            self._apikey = indexer.apikey

    def __get_params(self, keyword: str, mtype: MediaType = None, in_form: SearchType = None, page: Optional[int] = 0, pagesize: Optional[int] = None) -> dict:
        """
        获取请求参数
        """
        if not mtype and in_form == SearchType.TG:
            mode = "adult"
            categories = self._ninekg_category
        elif mtype == MediaType.TV:
            mode = "tvshow"
            categories = self._tv_category
        else:
            mode = "movie"
            categories = self._movie_category
        # mtorrent搜索imdb需要输入完整imdb链接，参见 https://wiki.m-team.cc/zh-tw/imdbtosearch
        if keyword and keyword.startswith("tt"):
            keyword = f'https://www.imdb.com/title/{keyword}'
        return {
            "mode": mode,
            "keyword": keyword,
            "categories": categories,
            "pageNumber": int(page) + 1,
            "pageSize": pagesize or self._size,
            "visible": 1
        }

    def __parse_result(self, results: List[dict]):
        """
        解析搜索结果
        """
        torrents = []
        if not results:
            return torrents

        for result in results:
            category_value = result.get('category')
            if category_value in self._tv_category \
                    and category_value not in self._movie_category:
                category = MediaType.TV.value
            elif category_value in self._movie_category:
                category = MediaType.MOVIE.value
            else:
                category = MediaType.UNKNOWN.value
            # 处理馒头新版标签
            labels = []
            labels_new = result.get('labelsNew')
            if labels_new:
                # 新版标签本身就是list
                labels = labels_new
            else:
                # 旧版标签
                labels_value = self._labels.get(result.get('labels') or "0") or ""
                if labels_value:
                    labels = labels_value.split()
            status = result.get('status', {})
            torrent = {
                'title': result.get('name'),
                'description': result.get('smallDescr'),
                'enclosure': self.__get_download_url(result.get('id')),
                'pubdate': StringUtils.timestamp_to_date(result.get('createdDate')),
                'size': int(result.get('size') or '0'),
                'seeders': int(status.get("seeders") or '0'),
                'peers': int(status.get("leechers") or '0'),
                'grabs': int(status.get("timesCompleted") or '0'),
                'downloadvolumefactor': self.__get_downloadvolumefactor(status.get("discount")),
                'uploadvolumefactor': self.__get_uploadvolumefactor(status.get("discount")),
                'page_url': self._pageurl % (self._url, result.get('id')),
                'imdbid': self.__find_imdbid(result.get('imdb')),
                'labels': labels,
                'category': category
            }
            if discount_end_time := status.get('discountEndTime'):
                torrent['freedate'] = StringUtils.timestr_to_dayhour(discount_end_time)
            # 解析全站促销时的规则(当前馒头只有下载促销)
            if promotion_rule := status.get("promotionRule"):
                discount = promotion_rule.get("discount", "NORMAL")
                torrent["downloadvolumefactor"] = self.__get_downloadvolumefactor(discount)
                if end_time := promotion_rule.get("endTime"):
                    torrent["freedate"] = StringUtils.timestr_to_dayhour(end_time)
            if mall_single_free := status.get("mallSingleFree"):
                if mall_single_free.get("status") == "ONGOING":
                    torrent["downloadvolumefactor"] = self.__get_downloadvolumefactor("FREE")
                    if end_date := mall_single_free.get("endDate"):
                        torrent["freedate"] = StringUtils.timestr_to_dayhour(end_date)
            torrents.append(torrent)
        return torrents

    def search(self, keyword: str, mtype: MediaType = None, in_from: SearchType = None, page: Optional[int] = 0, pagesize: Optional[int] = None) -> Tuple[bool, List[dict]]:
        """
        搜索
        """
        # 检查ApiKey
        if not self._apikey:
            return []

        # 获取请求参数
        params = self.__get_params(keyword, mtype, in_from, page, pagesize)

        # 发送请求
        res = RequestUtils(
            headers={
                "Content-Type": "application/json",
                "User-Agent": f'{self._ua}',
                "x-api-key": self._apikey
            },
            proxies=self._proxy,
            referer=f'{self._domain}browse',
            timeout=self._timeout
        ).post_res(url=self._searchurl, json=params)
        if res and res.status_code == 200:
            results = res.json().get('data', {}).get("data") or []
            return self.__parse_result(results)
        elif res is not None:
            logger.warn(f'{self._name} 搜索失败，错误码：{res.status_code}')
            return []
        else:
            logger.warn(f'{self._name} 搜索失败，无法连接 {self._domain}')
            return []

    @staticmethod
    def __find_imdbid(imdb: str) -> str:
        """
        从imdb链接中提取imdbid
        """
        if imdb:
            m = re.search(r"tt\d+", imdb)
            if m:
                return m.group(0)
        return ""

    @staticmethod
    def __get_downloadvolumefactor(discount: str) -> float:
        """
        获取下载系数
        """
        discount_dict = {
            "FREE": 0,
            "PERCENT_50": 0.5,
            "PERCENT_70": 0.3,
            "_2X_FREE": 0,
            "_2X_PERCENT_50": 0.5
        }
        if discount:
            return discount_dict.get(discount, 1)
        return 1

    @staticmethod
    def __get_uploadvolumefactor(discount: str) -> float:
        """
        获取上传系数
        """
        uploadvolumefactor_dict = {
            "_2X": 2.0,
            "_2X_FREE": 2.0,
            "_2X_PERCENT_50": 2.0
        }
        if discount:
            return uploadvolumefactor_dict.get(discount, 1)
        return 1

    def __get_download_url(self, torrent_id: str) -> str:
        """
        获取下载链接，返回base64编码的json字符串及URL
        """
        url = self._downloadurl % self._domain
        params = {
            'method': 'post',
            'cookie': False,
            'params': {
                'id': torrent_id
            },
            'header': {
                'User-Agent': f'{self._ua}',
                'Accept': 'application/json, text/plain, */*',
                'x-api-key': self._apikey
            },
            'proxy': True if self._proxy else False,
            'result': 'data'
        }
        # base64编码
        base64_str = base64.b64encode(json.dumps(params).encode('utf-8')).decode('utf-8')
        return f'[{base64_str}]{url}'
    
    def download_subtitles_by_pageurl(self, page_url: str, meta_name: str, download_dir: str):
        addr = urlparse(page_url)
        logger.info(f"【MTorrentSpider】下载馒头字幕 {page_url}")
        if not self._apikey:
            logger.warn(f"【MTorrentSpider】 获取馒头字幕失败, 未设置站点Api-Key")
            return
        # 从馒头的详情页网址中提取种子id
        torrent_id = urlparse(page_url).path.rsplit("/", 1)[-1].strip()
        try:
            subtitle_info_list = self.__get_subtitles_info_by_id(torrent_id, meta_name)
            sleep(10)
            for subtitle_info in subtitle_info_list:
                self.download_subtitle_by_url(torrent_id, subtitle_info, meta_name, download_dir)
                # 等待10s,避免请求失败
                sleep(10)
        except Exception as e:
            logger.error(f'{self._name} 获取字幕失败：{e}')

    def download_subtitle_by_url(self, torrentid, subtitle_info: dict, meta_name: str, download_dir: str) -> List[str]:
        """
        从下载链接下载字幕

        :param page_url: 种子详情页网址
        :type page_url: str
        :return: 字幕下载链接
        :rtype: List[str]
        """
        subtitle_id = subtitle_info.get("id")
        download_url = self.__subtitle_genlink(subtitle_id)
        filename = subtitle_info.get("filename")
        lang = subtitle_info.get("lang")
        meta_name = subtitle_info.get("metaname")
        res = RequestUtils(
            headers={
                'x-api-key': self._apikey,
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": self._ua,
                "Accept": "*/*"
            },
            proxies=self._proxy,
            timeout=30
        ).get_res(download_url)
        if res and res.status_code == 200:
            # 创建目录
            if not os.path.exists(download_dir):
                os.makedirs(download_dir, exist_ok=True)
            # 保存ZIP
            spli_filename = os.path.splitext(filename)
            if meta_name:
                file_name = (meta_name+'.'+subtitle_id+'.chi'+spli_filename[-1]) if lang == "25" else (meta_name+'.'+subtitle_id+spli_filename[-1])
            else:
                file_name = (spli_filename[0]+'.chi'+spli_filename[-1]) if lang == "25" and ".chi." not in filename else filename
            if not file_name:
                logger.warn(f"【MTorrentSpider】 馒头{torrentid} 字幕文件非法：{subtitle_id}")
                return
            save_tmp_path = Config().get_temp_path()
            if file_name.lower().endswith((".zip", ".tar")):
                # ZIP包
                zip_file = os.path.join(save_tmp_path, file_name)
                # 解压路径
                zip_path = os.path.splitext(zip_file)[0]
                with open(zip_file, 'wb') as f:
                    f.write(res.content)
                # 解压文件
                shutil.unpack_archive(zip_file, zip_path)
                # 遍历转移文件
                for sub_file in PathUtils.get_dir_files(in_path=zip_path, exts=RMT_SUBEXT):
                    target_sub_file = os.path.join(download_dir,os.path.basename(sub_file))
                    logger.info(f"【MTorrentSpider】 馒头{torrentid} 转移字幕 {sub_file} 到 {target_sub_file}")
                    SiteHelper.transfer_subtitle(sub_file, target_sub_file)
                # 删除临时文件
                try:
                    shutil.rmtree(zip_path)
                    os.remove(zip_file)
                except Exception as err:
                    ExceptionUtils.exception_traceback(err)
            else:
                sub_file = os.path.join(save_tmp_path, file_name)
                # 保存
                with open(sub_file, 'wb') as f:
                    f.write(res.content)
                target_sub_file = os.path.join(download_dir,os.path.basename(sub_file))
                logger.info(f"【MTorrentSpider】 馒头{torrentid} 转移字幕 {sub_file} 到 {target_sub_file}")
                SiteHelper.transfer_subtitle(sub_file, target_sub_file)
        elif res is not None:
            logger.warn(f"【MTorrentSpider】 下载馒头{torrentid}字幕 {filename} 失败，错误码：{res.status_code}")
        else:
            logger.warn(f"【MTorrentSpider】 下载馒头{torrentid}字幕 {filename} 失败，无法连接 {download_url}")   

    def __get_subtitles_info_by_id(self, torrent_id: str, meta_name: str) -> Optional[List[str]]:
        """
        获取指定种子的字幕列表
        """
        url = self._subtitle_list_url % self._domain
        # 发送请求
        res = RequestUtils(
            headers={
                "Accept": "application/json, text/plain, */*",
                "User-Agent": f'{self._ua}',
                "x-api-key": self._apikey,
            },
            proxies=self._proxy,
            timeout=self._timeout,
        ).post_res(url, params={"id": torrent_id})
        if res and res.status_code == 200:
            results = res.json()
            if int(results.get("code", -1)) == 0:
                subtitle_list = []
                for result in results.get("data", []):
                    subtitle = {
                        "id": result.get("id"),
                        "filename": result.get("filename"),
                        "lang": result.get("lang"),
                        "metaname": meta_name
                    }
                    subtitle_list.append(subtitle)
                return subtitle_list
            else:
                logger.warn(f'{self._name} 获取字幕列表失败，返回：{results.get("message", "未知")}')
                return None
        elif res is not None:
            logger.warn(f'{self._name} 获取字幕列表失败，错误码：{res.status_code}')
            return None
        else:
            logger.warn(f'{self._name} 获取字幕列表失败，无法连接 {self._domain}')
            return None

    def __subtitle_genlink(self, subtitle_id: str) -> Optional[str]:
        """
        获取字幕的下载链接
        """
        url = self._subtitle_genlink_url % self._domain
        # 发送请求
        res = RequestUtils(
            headers={
                "Accept": "application/json, text/plain, */*",
                "User-Agent": f'{self._ua}',
                "x-api-key": self._apikey,
            },
            proxies=self._proxy,
            timeout=self._timeout,
        ).post_res(url, params={"id": subtitle_id})
        if res and res.status_code == 200:
            result = res.json()
            if int(result.get("code", -1)) == 0 and isinstance(result.get("data"), str):
                return self._subtitle_download_url % (self._domain, result["data"])
            else:
                logger.warn(f'{self._name} 获取字幕下载链接失败，返回：{result.get("message", "未知")}')
                return None
        elif res is not None:
            logger.warn(f'{self._name} 获取字幕下载链接失败，错误码：{res.status_code}')
            return None
        else:
            logger.warn(f'{self._name} 获取字幕下载链接失败，无法连接 {self._domain}')
            return None

    # 获取种子的促销详情
    def check_torrent_attr(self, torrent_url):
        ret_attr = {
            "free": False,
            "2xfree": False,
            "hr": False,
            "peer_count": 0
        }
        addr = urlparse(torrent_url)
        # /detail/770**
        m = re.match("/detail/([0-9]+)", addr.path)
        if not m:
            logger.warn(f"【MTorrentSpider】 获取馒头种子属性失败 path：{addr.path}")
            return ret_attr
        torrentid = int(m.group(1))
        if not self._apikey:
            logger.warn("【MTorrentSpider】 获取馒头种子属性失败, 未设置站点Api-Key")
            return ret_attr
        site_url = "%s/api/torrent/detail" % StringUtils.get_base_url(torrent_url).replace("kp", "api")
        res = RequestUtils(
            headers={
                'x-api-key': self._apikey,
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": self._ua,
                "Accept": "application/json"
            },
            proxies=Config().get_proxies() if self._proxy else None,
            timeout=30
        ).post_res(url=site_url, params=("id=%d" % torrentid))
        if res and res.status_code == 200:
            msg = res.json().get('message')
            if msg != "SUCCESS":
                logger.warn(f"【MTorrentSpider】 获取馒头种子{torrentid}属性失败：{msg}")
                return ret_attr
            result = res.json().get('data', {})
            status = result.get('status')
            ret_attr["peer_count"] = int(status.get('seeders'))
            """
            NORMAL:上传下载都1倍
            _2X_FREE:上傳乘以二倍，下載不計算流量。
            _2X_PERCENT_50:上傳乘以二倍，下載計算一半流量。
            _2X:上傳乘以二倍，下載計算正常流量。
            PERCENT_50:上傳計算正常流量，下載計算一半流量。
            PERCENT_30:上傳計算正常流量，下載計算該種子流量的30%。
            FREE:上傳計算正常流量，下載不計算流量。
            """
            discount = status.get('discount')
            if discount == "_2X_FREE":
                ret_attr["2xfree"] = True
            elif discount == "FREE":
                ret_attr["free"] = True
        elif res is not None:
            logger.warn(f"【MTorrentSpider】 获取馒头种子{torrentid}属性失败，错误码：{res.status_code}")
        else:
            logger.warn(f"【MTorrentSpider】 获取馒头种子{torrentid}属性失败，无法连接 {site_url}")
        return ret_attr