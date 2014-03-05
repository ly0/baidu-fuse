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
import time
from fuse import FUSE, FuseOSError, Operations
from baidupcsapi import PCS
import logging
import tempfile
import progressbar


logger = logging.getLogger("BaiduFS")
formatter = logging.Formatter(
    '%(name)-12s %(asctime)s %(levelname)-8s %(message)s',
    '%a, %d %b %Y %H:%M:%S')

'''
logging.basicConfig(level=logging.DEBUG,
                format='%(asctime)s %(filename)s[line:%(lineno)d] %(levelname)s %(message)s',
                datefmt='%a, %d %b %Y %H:%M:%S')

'''
class NoSuchRowException(Exception):
    pass

class NoUniqueValueException(Exception):
    pass
class ProgressBar():
    def __init__(self):
        self.first_call = True
    def __call__(self, *args, **kwargs):
        if self.first_call:
            self.widgets = [progressbar.Percentage(), ' ', progressbar.Bar(marker=progressbar.RotatingMarker('>')),
                            ' ', progressbar.FileTransferSpeed()]
            self.pbar = progressbar.ProgressBar(widgets=self.widgets, maxval=kwargs['size']).start()
            self.first_call = False

        if kwargs['size'] <= kwargs['progress']:
            self.pbar.finish()
        else:
            self.pbar.update(kwargs['progress'])

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
        self.upload_blocks = {} # 文件上传时用于记录块的md5,{PATH:{TMP:'',BLOCKS:''}
        self.create_tmp = {} # {goutputstrem_path:file}
        self.upload_fails = {} #
        self.fd = 3
        # 初始化百度服务器
        print '设置pcs服务器'
        pcs = self.disk.get_fastest_pcs_server()
        self.disk.set_pcs_server(pcs)
        print 'pcs api server:',pcs
        '''
        print '设置百度网盘服务器,时间比较长,请耐心等待'
        pan = self.disk.get_fastest_mirror()
        self.disk.set_pan_server(pan)
        print 'baidupan server',pan
        '''

        self.uploadLock = Lock() # 上传文件时不刷新目录
        self.readLock = Lock()
        self.downloading_files = []
        
    def unlink(self, path):
        print '*'*10,'UNLINK CALLED',path
        self.disk.delete([path])


    def _add_file_to_buffer(self, path,file_info):
        foo = File()
        foo['st_ctime'] = file_info['local_ctime']
        foo['st_mtime'] = file_info['local_mtime']
        foo['st_mode'] = (stat.S_IFDIR | 0777) if file_info['isdir'] \
            else (stat.S_IFREG | 0777)
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
            #print path,'命中'
            return self.buffer[path].getDict()



    def readdir(self, path, offset):
        self.uploadLock.acquire()
        while True:
            try:
                foo = json.loads(self.disk.list_files(path).text)
                break
            except:
                print 'error'


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
                while 1:
                    try:
                        ret = json.loads(self.disk.meta(obj).text)
                        break
                    except:
                        print 'error'

                for file_info in ret['info']:
                    if not self.buffer.has_key(file_info['path']):
                        self._add_file_to_buffer(file_info['path'],file_info)
            #print self.buffer
            print '对',path,'的缓存完成'
            self.traversed_folder[path] = True
        for r in files:
            yield r
        self.uploadLock.release()

    def _update_file_manual(self,path):
        while 1:
            try:
                jdata = json.loads(self.disk.meta([path]).content)
                break
            except:
                print 'error'

        if 'info' not in jdata:
            raise FuseOSError(errno.ENOENT)
        if jdata['errno'] != 0:
            raise FuseOSError(errno.ENOENT)
        file_info = jdata['info'][0]
        self._add_file_to_buffer(path,file_info)


    def rename(self, old, new):
        #logging.debug('* rename',old,os.path.basename(new))
        print '*'*10,'RENAME CALLED',old,os.path.basename(new),type(old),type(new)
        while True:
            try:
                ret = self.disk.rename([(old,os.path.basename(new))]).content
                jdata = json.loads(ret)
                break
            except:
                print 'error'

        if jdata['errno'] != 0:
            # 文件名已存在,删除原文件
            print self.disk.delete([new]).content
            print self.disk.rename([(old,os.path.basename(new))])
        self._update_file_manual(new)
        self.buffer.pop(old)


    def open(self, path, flags):
        self.readLock.acquire()
        print '*'*10,'OPEN CALLED',path,flags
        #print '[****]',path
        """
        Permission denied

        accmode = os.O_RDONLY | os.O_WRONLY | os.O_RDWR
        if (flags & accmode) != os.O_RDONLY:
            raise FuseOSError(errno.EACCES)
        """
        self.fd += 1
        self.readLock.release()
        
        return self.fd

    def create(self, path, mode,fh=None):
        # 创建文件
        # 中文路径有问题
        print '*'*10,'CREATE CALLED',path,mode,type(path)
        #if 'outputstream' not in path:
        tmp_file = tempfile.TemporaryFile('r+w+b')
        foo = self.disk.upload(os.path.dirname(path),tmp_file,os.path.basename(path)).content
        ret = json.loads(foo)
        print ret
        print 'create-not-outputstream',ret
        if ret['path'] != path:
            # 文件已存在
            print '文件已存在'
            raise FuseOSError(errno.EEXIST)
        '''
        else:
            print 'create:',path
            foo = File()
            foo['st_ctime'] = int(time.time())
            foo['st_mtime'] = int(time.time())
            foo['st_mode'] = (stat.S_IFREG | 0777)
            foo['st_nlink'] = 1
            foo['st_size'] = 0
            self.buffer[path] = foo
        '''


        '''
        dict(st_mode=(stat.S_IFREG | mode), st_nlink=1,
                                st_size=0, st_ctime=time.time(), st_mtime=time.time(),
                                st_atime=time.time())
        '''
        self.fd += 1
        return 0

    def write(self, path, data, offset, fp):
        # 上传文件时会调用
        # 4kb ( 4096 bytes ) 每块，data中是块中的数据
        # 最后一块的判断：len(data) < 4096
        # 文件大小 = 最后一块的offset + len(data)

        # 4kb传太慢了，合计成2M传一次

        #print '*'*10,path,offset, len(data)

        def _block_size(stream):
            stream.seek(0,2)
            return stream.tell()

        _BLOCK_SIZE = 16 * 2 ** 20
        # 第一块的任务
        if offset == 0:
            #self.uploadLock.acquire()
            #self.readLock.acquire()
            # 初始化块md5列表
            self.upload_blocks[path] = {'tmp':None,
                                        'blocks':[]}
            # 创建缓冲区临时文件
            tmp_file = tempfile.TemporaryFile('r+w+b')
            self.upload_blocks[path]['tmp'] = tmp_file

        # 向临时文件写入数据，检查是否>= _BLOCK_SIZE 是则上传该块并将临时文件清空
        try:
            tmp = self.upload_blocks[path]['tmp']
        except KeyError:
            return 0
        tmp.write(data)

        if _block_size(tmp) > _BLOCK_SIZE:
            print path,'发生上传'
            tmp.seek(0)
            try:
                foo = self.disk.upload_tmpfile(tmp,callback=ProgressBar()).content
                foofoo = json.loads(foo)
                block_md5 = foofoo['md5']
            except:
                 print foo



            # 在 upload_blocks 中插入本块的 md5
            self.upload_blocks[path]['blocks'].append(block_md5)
            # 创建缓冲区临时文件
            self.upload_blocks[path]['tmp'].close()
            tmp_file = tempfile.TemporaryFile('r+w+b')
            self.upload_blocks[path]['tmp'] = tmp_file
            print '创建临时文件',tmp_file.name

        # 最后一块的任务
        if len(data) < 4096:
            # 检查是否有重名，有重名则删除它
            while True:
                try:
                    foo = self.disk.meta([path]).content
                    foofoo = json.loads(foo)
                    break
                except:
                    print 'error'


            if foofoo['errno'] == 0:
                logging.debug('Deleted the file which has same name.')
                self.disk.delete([path])
            # 看看是否需要上传
            if _block_size(tmp) != 0:
                # 此时临时文件有数据，需要上传
                print path,'发生上传,块末尾,文件大小',_block_size(tmp)
                tmp.seek(0)
                while True:
                    try:
                        foo = self.disk.upload_tmpfile(tmp,callback=ProgressBar()).content
                        foofoo = json.loads(foo)
                        break
                    except:
                        print 'exception, retry.'

                block_md5 = foofoo['md5']
                # 在 upload_blocks 中插入本块的 md5
                self.upload_blocks[path]['blocks'].append(block_md5)

            # 调用 upload_superfile 以合并块文件
            print '合并文件',path,type(path)
            self.disk.upload_superfile(path,self.upload_blocks[path]['blocks'])
            # 删除upload_blocks中数据
            self.upload_blocks.pop(path)
            # 更新本地文件列表缓存
            self._update_file_manual(path)
            #self.readLock.release()
            #self.uploadLock.release()
        return len(data)


    def mkdir(self, path, mode):
        logger.debug("mkdir is:" + path)
        self.disk.mkdir(path)

    def rmdir(self, path):
        logger.debug("rmdir is:" + path)
        self.disk.delete([path])

    def read(self, path, size, offset, fh):
        #print '*'*10,'READ CALLED',path,size,offset
        #logger.debug("read is: " + path)
        paras = {'Range': 'bytes=%s-%s' % (offset, offset + size - 1)}
        return self.disk.download(path, headers=paras).content

    access = None
    statfs = None

if __name__ == '__main__':
    if len(sys.argv) != 4:
        print 'Usage {0} username password mountpoint'.format(sys.argv[0])
        sys.exit(0)
    FUSE(BaiduFS(sys.argv[1],sys.argv[2]),sys.argv[3],foreground=True, nonempty=True)
