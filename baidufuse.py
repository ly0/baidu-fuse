#!/usr/bin/python
# -*- coding: utf-8 -*-

import stat
import errno
import os
import sys
import math
from threading import Lock
try:
    import _find_fuse_parts
except ImportError:
    pass
import json
from fuse import FUSE, FuseOSError, Operations
from baidupcsapi import PCS
import logging

baidu_rootdir = '/'
logger = logging.getLogger("BaiduFS")
formatter = logging.Formatter(
    '%(name)-12s %(asctime)s %(levelname)-8s %(message)s',
    '%a, %d %b %Y %H:%M:%S')
#file_handler = logging.Handler(level=0)
#file_handler.setFormatter(formatter)
#logger.addHandler(file_handler)

class NoSuchRowException(Exception):
    pass

class NoUniqueValueException(Exception):
    pass

class File():
    def __init__(self):
        self.dict = {'bd_fsid':0,
                    'bd_blocklist':0,
                    'bd_md5':0,
                    'st_mode':0,
                    'st_ino':0,
                    'st_dev':0,
                    'st_nlink':0,
                    'st_uid':0,
                    'st_gid':0,
                    'st_size':0,
                    'st_atime':0,
                    'st_mtime':0,
                    'st_ctime':0}
    def __getitem__(self, item):
        return self.dict[item]
    def __setitem__(self, key, value):
        self.dict[key] = value
    def __str__(self):
        return self.dict.__repr__()
    def __repr__(self):
        return self.dict.__repr__()
    def getDict(self):
        return self.dict

class BaiduFS(Operations):
    '''Baidu netdisk filesystem'''

    def __init__(self, username, password, *args, **kw):
        self.disk = PCS(username, password)
        self.buffer = {}
        self.traversed_folder = {}
        self.bufferLock = Lock()

    def _add_file_to_buffer(self, path,file_info):
        foo = File()
        foo['st_ctime'] = file_info['local_ctime']
        foo['st_mtime'] = file_info['local_mtime']
        foo['st_mode'] = (stat.S_IFDIR | 0666) if file_info['isdir'] \
            else (stat.S_IFREG | 0666)
        foo['st_nlink'] = 2 if file_info['isdir'] else 1
        foo['st_size'] = file_info['size']
        self.buffer[path] = foo

    def _del_file_from_buffer(self,path):
        self.buffer.pop(path)

    def getattr(self, path, fh=None):
        #print 'getattr *',path
        # 先看缓存中是否存在该文件

        if not self.buffer.has_key(path):
            print path,'未命中'
            #print self.buffer
            #print self.traversed_folder
            jdata = json.loads(self.disk.meta([path]).content)
            try:
                if 'info' not in jdata:
                    raise FuseOSError(errno.ENOENT)
                if jdata['errno'] != 0:
                    raise FuseOSError(errno.ENOENT)
                file_info = jdata['info'][0]
                self._add_file_to_buffer(path,file_info)
                st = self.buffer[path].getDict()
                return st
            except:
                raise FuseOSError(errno.ENOENT)
        else:
            print path,'命中'
            return self.buffer[path].getDict()



    def readdir(self, path, offset):
        foo = json.loads(self.disk.list_files(path).text)
        files = ['.', '..']
        abs_files = [] # 该文件夹下文件的绝对路径
        for file in foo['list']:
            files.append(file['server_filename'])
            abs_files.append(file['path'])
        # 缓存文件夹下文件信息,批量查询meta info

        # Update:解决meta接口一次不能查询超过100条记录
        # 分成 ceil(file_num / 100.0) 组，利用商群
        if not self.traversed_folder.has_key(path) or self.traversed_folder[path] == False:
            print '正在对',path,'缓存中'
            file_num = len(abs_files)
            group = int(math.ceil(file_num / 100.0))
            for i in range(group):
                obj = [f for n,f in enumerate(abs_files) if n % group == i] #一组数据
                ret = json.loads(self.disk.meta(obj).text)
                for file_info in ret['info']:
                    if not self.buffer.has_key(file_info['path']):
                        self._add_file_to_buffer(file_info['path'],file_info)
            #print self.buffer
            print '对',path,'的缓存完成'
            self.traversed_folder[path] = True
        for r in files:
            yield r

    def open(self, path, flags):
        logger.error("open is: " + path)
        pass
        accmode = os.O_RDONLY | os.O_WRONLY | os.O_RDWR
        if (flags & accmode) != os.O_RDONLY:
            return FuseOSError(errno.EACCES)


    def mkdir(self, path, mode):
        logger.debug("mkdir is:" + path)
        abs_path = self.get_abs_path(path)
        self.disk.mkdir(abs_path)

    def rmdir(self, path):
        logger.debug("rmdir is:" + path)
        abs_path = self.get_abs_path(path)
        self.disk.delete(abs_path)

    def read(self, path, size, offset):
        logger.debug("read is: " + path)
        abs_path = self.get_abs_path(path)
        paras = {'Range': 'bytes=%s-%s' % (offset, offset + size)}
        return self.disk.download(abs_path, paras).content

    access = None
    statfs = None

if __name__ == '__main__':
    if len(sys.argv) != 4:
		print 'Usage {0} username password mountpoint'.format(sys.argv[0])
		sys.exit(0)
    FUSE(BaiduFS(sys.argv[1],sys.argv[2]),sys.argv[3],foreground=True, nonempty=True)
