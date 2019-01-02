import torch.utils.data as data
import numpy as np
import torch
import cv2
import config
import os
import glob
import sys
sys.path.append("../")
from utils.img import Crop
from util import Rnd, Flip, rot2Quaternion,angular_distance_np
import util
import warnings
from scipy.sparse import csc_matrix
from sklearn.neighbors import KDTree


class SUNCG(data.Dataset):
  def __init__(self, split, nViews, AuthenticdepthMap=False, crop=False, cache=True,\
        hmap=False,CorresCoords=False,meta=False,rotate=False,rgbd=False,birdview=False,pointcloud=False,num_points=None,
        classifier=False,segm=False,segm_pyramid=False,normal=False,normal_pyramid=False,walls=False,gridPC=False,edges=False,samplePattern='',
        list_=None,singleView=True,siftFeatCorres=False,debug=False,orbfeat=False,siftPoint=False,denseCorres=False,reproj=False
        ,representation='skybox',entrySplit=None,dynamicWeighting=False,snumclass=0):
    self.crop = crop
    self.pointcloud = pointcloud
    self.birdview = birdview
    self.num_points = num_points
    self.rgbd = rgbd
    self.rotate = rotate
    self.meta = meta
    self.walls = walls
    self.AuthenticdepthMap = AuthenticdepthMap
    self.hmap = hmap
    self.segm = segm
    self.segm_pyramid = segm_pyramid
    self.representation = representation
    self.normal = normal
    self.normal_pyramid = normal_pyramid
    self.samplePattern=samplePattern
    self.gridPC = gridPC
    self.edges = edges
    self.classifier = classifier
    self.CorresCoords = CorresCoords
    self.split = split
    self.nViews = nViews
    self.singleView = singleView
    self.debug = debug
    self.siftFeatCorres = siftFeatCorres
    self.orbfeat = orbfeat
    self.siftPoint=siftPoint
    self.denseCorres=denseCorres
    self.reproj=reproj
    self.entrySplit=entrySplit
    self.dynamicWeighting = dynamicWeighting
    if self.dynamicWeighting:
      assert(self.segm == True)
    self.snumclass = snumclass
    self.list = list_
    self.OutputSize = (640,160)
    self.Inputwidth = config.pano_width
    self.Inputheight = config.pano_height
    self.nPanoView = 4
    self.cut = 224
    self.intrinsic = np.array([[571.623718/640,0,319.500000/640],[0,571.623718/480,239.500000/480],[0,0,1]])
    self.intrinsicUnNorm = np.array([[571.623718,0,319.500000],[0,571.623718,239.500000],[0,0,1]])
    self.dataList = np.load(self.list).item()[self.split]

    if self.entrySplit is not None:
      self.dataList = [self.dataList[kk] for kk in range(self.entrySplit*100,(self.entrySplit+1)*100)]
    self.len = len(self.dataList)

    Rs = np.zeros([4,4,4])
    Rs[0] = np.eye(4)
    Rs[1] = np.array([[0,0,-1,0],[0,1,0,0],[1,0,0,0],[0,0,0,1]])
    Rs[2] = np.array([[-1,0,0,0],[0,1,0,0],[0,0,-1,0],[0,0,0,1]])
    Rs[3] = np.array([[0,0,1,0],[0,1,0,0],[-1,0,0,0],[0,0,0,1]])
    self.Rs = Rs
    self.sift = cv2.xfeatures2d.SIFT_create()

  def LoadImage(self, PATH,depth=True):
    if depth:
      img = cv2.imread(PATH,2)/1000.
    else:
      img = cv2.imread(PATH) # load in rgb format
    return img
  
  def shuffle(self):
    pass
  
  def __getpair__(self, index):
    self.base_this = self.dataList[index]['base']
    self.interval_this = '0-15'
    ct0,ct1=self.dataList[index]['id_src'],self.dataList[index]['id_tgt']

    return ct0,ct1
  
  def Pano2PointCloud(self,depth):
    assert(depth.shape[0]==160 and depth.shape[1]==640)
    w,h = depth.shape[1]//4, depth.shape[0]
    ys, xs = np.meshgrid(range(h),range(w),indexing='ij')
    ys, xs = (0.5-ys / h)*2, (xs / w-0.5)*2
    pc = []
    for i in range(4):
      zs = depth[:,i*w:(i+1)*w].flatten()
      ys_this, xs_this = ys.flatten()*zs, xs.flatten()*zs
      pc_this = np.concatenate((xs_this,ys_this,-zs)).reshape(3,-1) # assume depth clean
      pc_this = np.matmul(self.Rs[i][:3,:3],pc_this)
      pc.append(pc_this)
    pc = np.concatenate(pc,1)
    return pc

  def get3Dpt(self,pts,depth,normal):
    # get the interpolated depth value
    tp = np.floor(pts).astype('int')
    v1 = depth[tp[:,1],tp[:,0]]
    v2 = depth[tp[:,1],tp[:,0]+1]
    v3 = depth[tp[:,1]+1,tp[:,0]]
    v4 = depth[tp[:,1]+1,tp[:,0]+1]
    val = v1*(tp[:,1]+1-pts[:,1])*(tp[:,0]+1-pts[:,0]) + \
        v2*(pts[:,0]-tp[:,0])*(tp[:,1]+1-pts[:,1]) + \
        v3*(pts[:,1]-tp[:,1])*(tp[:,0]+1-pts[:,0]) + \
        v4*(pts[:,0]-tp[:,0])*(pts[:,1]-tp[:,1])


    v1 = normal[tp[:,1],tp[:,0],:]
    v2 = normal[tp[:,1],tp[:,0]+1,:]
    v3 = normal[tp[:,1]+1,tp[:,0],:]
    v4 = normal[tp[:,1]+1,tp[:,0]+1,:]
    nn = v1*(tp[:,1]+1-pts[:,1])[:,np.newaxis]*(tp[:,0]+1-pts[:,0])[:,np.newaxis] + \
        v2*(pts[:,0]-tp[:,0])[:,np.newaxis]*(tp[:,1]+1-pts[:,1])[:,np.newaxis] + \
        v3*(pts[:,1]-tp[:,1])[:,np.newaxis]*(tp[:,0]+1-pts[:,0])[:,np.newaxis] + \
        v4*(pts[:,0]-tp[:,0])[:,np.newaxis]*(pts[:,1]-tp[:,1])[:,np.newaxis]
    nn /= np.linalg.norm(nn,axis=1,keepdims=True)

    xs,ys = pts[:,0],pts[:,1]
    # get 3d location of sift point
    h = depth.shape[0]
    w = h

    pc = []
    Rs = np.zeros([4,4,4])
    Rs[0] = np.eye(4)
    Rs[1] = np.array([[0,0,-1,0],[0,1,0,0],[1,0,0,0],[0,0,0,1]])
    Rs[2] = np.array([[-1,0,0,0],[0,1,0,0],[0,0,-1,0],[0,0,0,1]])
    Rs[3] = np.array([[0,0,1,0],[0,1,0,0],[-1,0,0,0],[0,0,0,1]])
    
    for i in range(len(xs)):
        idx = int(xs[i]//w)
        R_this = Rs[idx].copy()
        ystp, xstp = (0.5-ys[i] / h)*2, ((xs[i]-idx*w) / w-0.5)*2
        zstp = val[i]
        ystp, xstp = ystp*zstp, xstp*zstp
        tmp = np.concatenate(([xstp],[ystp],[-zstp]))
        tmp = np.matmul(R_this[:3,:3],tmp)+R_this[:3,3]
        pc.append(tmp)

        R_this = Rs[idx].copy()
        nn[i,:] = np.matmul(R_this[:3,:3],nn[i,:])

    pc = np.concatenate(pc).reshape(-1,3)

    return pc,nn

  def PanoIdx(self,index,h,w):
    total=h*w
    single=total//4
    hidx = index//single
    rest=index % single
    ys,xs=np.unravel_index(rest, [h,h])
    xs += hidx*h
    idx = np.zeros([len(xs),2])
    idx[:,0]=xs
    idx[:,1]=ys
    return idx

  def reproj_helper(self,pct,colorpct,out_shape,mode):
    # find which plane they intersect with
      h=out_shape[0]
      tp=pct.copy()
      tp[:2,:]/=(np.abs(tp[2,:])+1e-32)
      intersectf=(tp[2,:]<0)*(np.abs(tp[0,:])<1)*(np.abs(tp[1,:])<1)
      if mode in ['color','normal']:
        colorf=colorpct[intersectf,:]
      elif mode == 'depth':
        colorf=-tp[2,intersectf]
      coordf=tp[:2,intersectf]
      coordf[0,:]=(coordf[0,:]+1)*0.5*h
      coordf[1,:]=(1-coordf[1,:])*0.5*h
      coordf=coordf.round().clip(0,h-1).astype('int')

      tp=np.matmul(self.Rs[1][:3,:3].T,pct)
      tp[:2,:]/=(np.abs(tp[2,:])+1e-32)
      intersectr=(tp[2,:]<0)*(np.abs(tp[0,:])<1)*(np.abs(tp[1,:])<1)

      if mode in ['color','normal']:
        colorr=colorpct[intersectr,:]
      elif mode == 'depth':
        colorr=-tp[2,intersectr]

      coordr=tp[:2,intersectr]
      coordr[0,:]=(coordr[0,:]+1)*0.5*h
      coordr[1,:]=(1-coordr[1,:])*0.5*h
      coordr=coordr.round().clip(0,h-1).astype('int')
      coordr[0,:]+=h

      tp=np.matmul(self.Rs[2][:3,:3].T,pct)
      tp[:2,:]/=(np.abs(tp[2,:])+1e-32)
      intersectb=(tp[2,:]<0)*(np.abs(tp[0,:])<1)*(np.abs(tp[1,:])<1)

      if mode in ['color','normal']:
        colorb=colorpct[intersectb,:]
      elif mode == 'depth':
        colorb=-tp[2,intersectb]

      coordb=tp[:2,intersectb]
      coordb[0,:]=(coordb[0,:]+1)*0.5*h
      coordb[1,:]=(1-coordb[1,:])*0.5*h
      coordb=coordb.round().clip(0,h-1).astype('int')
      coordb[0,:]+=h*2

      tp=np.matmul(self.Rs[3][:3,:3].T,pct)
      tp[:2,:]/=(np.abs(tp[2,:])+1e-32)
      intersectl=(tp[2,:]<0)*(np.abs(tp[0,:])<1)*(np.abs(tp[1,:])<1)

      if mode in ['color','normal']:
        colorl=colorpct[intersectl,:]
      elif mode == 'depth':
        colorl=-tp[2,intersectl]

      coordl=tp[:2,intersectl]
      coordl[0,:]=(coordl[0,:]+1)*0.5*h
      coordl[1,:]=(1-coordl[1,:])*0.5*h
      coordl=coordl.round().clip(0,h-1).astype('int')
      coordl[0,:]+=h*3

      proj=np.zeros(out_shape)

      proj[coordf[1,:],coordf[0,:]]=colorf
      proj[coordl[1,:],coordl[0,:]]=colorl
      proj[coordb[1,:],coordb[0,:]]=colorb
      proj[coordr[1,:],coordr[0,:]]=colorr
      return proj
  def __getitem__(self, index):
    rets = {}
    imgs_ = np.zeros((self.nViews, *self.OutputSize[::-1]), dtype = np.float32)
    imgs = np.zeros((self.nViews, self.Inputheight, self.Inputwidth), dtype = np.float32)
    if self.rgbd:
      imgs_rgb = np.zeros((self.nViews, self.Inputheight, self.Inputwidth,3), dtype = np.float32)
      imgs_rgb_ = np.zeros((self.nViews,3,*self.OutputSize[::-1]), dtype = np.float32)
    if self.hmap:
      hmap = np.zeros((self.nViews,3,64, 64), dtype = np.float32)
    if self.birdview:
      imgs_bv = np.zeros((self.nViews, self.Inputheight, self.Inputwidth,3), dtype = np.float32)
      imgs_bv_ = np.zeros((self.nViews,3,*self.OutputSize[::-1]), dtype = np.float32)
    if self.pointcloud:
      pointcloud = np.zeros((self.nViews, 3, self.num_points), dtype = np.float32)
    R = np.zeros((self.nViews, 4, 4))
    Q = np.zeros((self.nViews, 7))
    assert(self.nViews == 2)
    imgsPath = []
    if self.AuthenticdepthMap: AuthenticdepthMap = np.zeros((self.nViews, *self.OutputSize[::-1]), dtype = np.float32)
    ct0,ct1 = self.__getpair__(index)
    
    if self.segm:
      segm = np.zeros((self.nViews,1,*self.OutputSize[::-1]), dtype = np.float32)
    if self.normal:
      normal = np.zeros((self.nViews,3,self.Inputheight,self.Inputwidth), dtype = np.float32)

    basePath = self.base_this
    frameid0 = f"{ct0:06d}"
    frameid1 = f"{ct1:06d}"

    imgs[0] = self.LoadImage(os.path.join(basePath,'depth','{}.png'.format(frameid0))).copy()
    imgs[1] = self.LoadImage(os.path.join(basePath,'depth','{}.png'.format(frameid1))).copy()
    dataMask = np.zeros((self.nViews, 1,*self.OutputSize[::-1]), dtype = np.float32)
    dataMask[0,0,:,:]=(imgs[0]!=0)
    dataMask[1,0,:,:]=(imgs[1]!=0)
    rets['dataMask']=dataMask[np.newaxis,:]
    
    if self.pointcloud:
      pc = util.DepthToPointCloud(imgs[0],self.intrinsicUnNorm)
      pointcloud[0] = pc[np.random.choice(range(len(pc)),self.num_points),:].T
      pc = util.DepthToPointCloud(imgs[1],self.intrinsicUnNorm)
      pointcloud[1] = pc[np.random.choice(range(len(pc)),self.num_points),:].T
    if self.birdview:
      imgs_bv[0] = self.LoadImage(os.path.join(basePath,'BirdView','{}.birdview.png'.format(frameid0)),depth=False).copy()/255.
      imgs_bv[1] = self.LoadImage(os.path.join(basePath,'BirdView','{}.birdview.png'.format(frameid1)),depth=False).copy()/255.
    if self.rgbd:
      imgs_rgb[0] = self.LoadImage(os.path.join(basePath,'rgb','{}.png'.format(frameid0)),depth=False).copy()/255.
      imgs_rgb[1] = self.LoadImage(os.path.join(basePath,'rgb','{}.png'.format(frameid1)),depth=False).copy()/255.

    R[0] = np.loadtxt(os.path.join(basePath,'pose', frameid0 + '.pose.txt'))
    R[1] = np.loadtxt(os.path.join(basePath,'pose', frameid1 + '.pose.txt'))
    #R[1] = R[0] = np.eye(4)
    Q[0,:4] = rot2Quaternion(R[0][:3,:3])
    Q[0,4:] = R[0][:3,3]
    Q[1,:4] = rot2Quaternion(R[1][:3,:3])
    Q[1,4:] = R[1][:3,3]

    if self.normal:
      normal[0] =self.LoadImage(os.path.join(basePath,'normal','{}.png'.format(frameid0)),depth=False).copy().transpose(2,0,1)/255.*2-1
      normal[1] =self.LoadImage(os.path.join(basePath,'normal','{}.png'.format(frameid1)),depth=False).copy().transpose(2,0,1)/255.*2-1
      #print(f"normalmean:{np.mean(np.power(normal[0],2).sum(0))},{np.mean(np.power(normal[1],2).sum(0))}\n")
      if self.normal_pyramid:
        a = int(outS(self.height))#41
        b = int(outS(self.height*0.5+1))#21
        normal_ = [resize_label_batch(normal.transpose(2,3,1,0),i).transpose(3,2,0,1) for i in [a,a,b,a]]
        normal_ = [m.reshape(1,self.nViews,3,m.shape[2],m.shape[3])for m in normal_]
      else:
        normal_ = np.zeros((self.nViews,3,*self.OutputSize[::-1]), dtype = np.float32)
        normal_[0] = cv2.resize(normal[0].transpose(1,2,0),self.OutputSize,interpolation=cv2.INTER_NEAREST).transpose(2,0,1)
        normal_[1] = cv2.resize(normal[1].transpose(1,2,0),self.OutputSize,interpolation=cv2.INTER_NEAREST).transpose(2,0,1)
        normal_ = normal_[np.newaxis,:]

    if self.denseCorres:
        # get 3d point cloud for each pano
        pcs = self.Pano2PointCloud(imgs[0]) # be aware of the order of returned pc!!!
        pct = self.Pano2PointCloud(imgs[1])
        #pct = np.matmul(R[0],np.matmul(np.linalg.inv(R[1]),np.concatenate((pct,np.ones([1,pct.shape[1]])))))[:3,:]
        pct = np.matmul(np.linalg.inv(R[1]),np.concatenate((pct,np.ones([1,pct.shape[1]]))))[:3,:]
        pcs = np.matmul(np.linalg.inv(R[0]),np.concatenate((pcs,np.ones([1,pcs.shape[1]]))))[:3,:]
        # find correspondence using kdtree
        tree = KDTree(pct.T)
        IdxQuery=np.random.choice(range(pcs.shape[1]),5000)
        # sample 5000 query points
        pcsQuery = pcs[:,IdxQuery]
        nearest_dist, nearest_ind = tree.query(pcsQuery.T, k=1)
        hasCorres=(nearest_dist < 0.08)
        idxTgtNeg=[]

        idxSrc=self.PanoIdx(IdxQuery[np.where(hasCorres)[0]],160,640)
        idxTgt=self.PanoIdx(nearest_ind[hasCorres],160,640)
        if hasCorres.sum() < 500:
          rets['denseCorres']={'idxSrc':np.zeros([1,2000,2]),'idxTgt':np.zeros([1,2000,2]),'valid':np.array([0]),'idxTgtNeg':idxTgtNeg}

        else:
          # only pick 2000 correspondence per pair
          idx2000 = np.random.choice(range(idxSrc.shape[0]),2000)
          idxSrc=idxSrc[idx2000][np.newaxis,:]
          idxTgt=idxTgt[idx2000][np.newaxis,:]
          rets['denseCorres']={'idxSrc':idxSrc,'idxTgt':idxTgt,'valid':np.array([1]),'idxTgtNeg':idxTgtNeg}

    # reprojct the second image into the first image plane
    if self.reproj:
      h=imgs.shape[1]
      colorpct=[]
      normalpct=[]
      depthpct=[]
      for ii in range(4):
        colorpct.append(imgs_rgb[1,:,ii*h:(ii+1)*h,:].reshape(-1,3))
        normalpct.append(normal_[0,1,:,:,ii*h:(ii+1)*h].reshape(3,-1))
        depthpct.append(imgs[1,:,ii*h:(ii+1)*h].reshape(-1))
      colorpct=np.concatenate(colorpct,0)
      normalpct=np.concatenate(normalpct,1)
      depthpct=np.concatenate(depthpct)
      # get the coordinates of each point in the first coordinate system
      pct = self.Pano2PointCloud(imgs[1])# be aware of the order of returned pc!!!
      R_this=np.matmul(R[0],np.linalg.inv(R[1]))
      R_this_p=R_this.copy()
      dR=util.randomRotation(epsilon=0.1)
      dRangle=angular_distance_np(dR[np.newaxis,:],np.eye(3)[np.newaxis,:])[0]
      
      R_this_p[:3,:3]=np.matmul(dR,R_this_p[:3,:3])
      R_this_p[:3,3]+=np.random.randn(3)*0.1

      t2s_dr = np.matmul(R_this, np.linalg.inv(R_this_p))
      
      pct_reproj = np.matmul(R_this_p,np.concatenate((pct,np.ones([1,pct.shape[1]]))))[:3,:]
      pct_reproj_org = np.matmul(R_this,np.concatenate((pct,np.ones([1,pct.shape[1]]))))[:3,:]
      flow = pct_reproj_org - pct_reproj
      #if np.abs(pct).min()==0:
      #    import ipdb;ipdb.set_trace()
      # assume always observe the second view(right view)
      colorpct=colorpct[h*h:h*h*2,:]
      depthpct=depthpct[h*h:h*h*2]
      normalpct=normalpct[:,h*h:h*h*2]
      #normalpct=np.matmul(R_this[:3,:3], normalpct).T # used to be a mistake!
      normalpct=np.matmul(R_this_p[:3,:3], normalpct).T
      pct_reproj=pct_reproj[:,h*h:h*h*2]
      pct_reproj_org=pct_reproj_org[:,h*h:h*h*2]
      flow = flow[:,h*h:h*h*2].T


      t2s_rgb=self.reproj_helper(pct_reproj_org,colorpct,imgs_rgb[0].shape,'color')
      t2s_rgb_p=self.reproj_helper(pct_reproj,colorpct,imgs_rgb[0].shape,'color')
      t2s_n_p=self.reproj_helper(pct_reproj,normalpct,imgs_rgb[0].shape,'normal')
      t2s_d_p=self.reproj_helper(pct_reproj,depthpct,imgs_rgb[0].shape[:2],'depth')
      
      t2s_flow_p=self.reproj_helper(pct_reproj,flow,imgs_rgb[0].shape,'color')
      t2s_mask_p=(t2s_d_p!=0).astype('int')

      #import ipdb;ipdb.set_trace()
      colorpct=[]
      normalpct=[]
      depthpct=[]
      for ii in range(4):
        colorpct.append(imgs_rgb[0,:,ii*h:(ii+1)*h,:].reshape(-1,3))
        normalpct.append(normal_[0,0,:,:,ii*h:(ii+1)*h].reshape(3,-1))
        depthpct.append(imgs[0,:,ii*h:(ii+1)*h].reshape(-1))
      colorpct=np.concatenate(colorpct,0)
      normalpct=np.concatenate(normalpct,1)
      depthpct=np.concatenate(depthpct)
      # get the coordinates of each point in the first coordinate system
      pct = self.Pano2PointCloud(imgs[0])# be aware of the order of returned pc!!!
      R_this=np.matmul(R[1],np.linalg.inv(R[0]))
      R_this_p=R_this.copy()
      dR=util.randomRotation(epsilon=0.1)
      dRangle=angular_distance_np(dR[np.newaxis,:],np.eye(3)[np.newaxis,:])[0]
      R_this_p[:3,:3]=np.matmul(dR,R_this_p[:3,:3])
      R_this_p[:3,3]+=np.random.randn(3)*0.1
      s2t_dr = np.matmul(R_this, np.linalg.inv(R_this_p))
      pct_reproj = np.matmul(R_this_p,np.concatenate((pct,np.ones([1,pct.shape[1]]))))[:3,:]
      pct_reproj_org = np.matmul(R_this,np.concatenate((pct,np.ones([1,pct.shape[1]]))))[:3,:]
      flow = pct_reproj_org - pct_reproj
      # assume always observe the second view(right view)
      colorpct=colorpct[h*h:h*h*2,:]
      depthpct=depthpct[h*h:h*h*2]
      normalpct=normalpct[:,h*h:h*h*2]
      normalpct=np.matmul(R_this_p[:3,:3], normalpct).T
      pct_reproj=pct_reproj[:,h*h:h*h*2]
      pct_reproj_org=pct_reproj_org[:,h*h:h*h*2]
      flow = flow[:,h*h:h*h*2].T

      s2t_rgb=self.reproj_helper(pct_reproj_org,colorpct,imgs_rgb[0].shape,'color')
      s2t_rgb_p=self.reproj_helper(pct_reproj,colorpct,imgs_rgb[0].shape,'color')
      s2t_n_p=self.reproj_helper(pct_reproj,normalpct,imgs_rgb[0].shape,'normal')
      s2t_d_p=self.reproj_helper(pct_reproj,depthpct,imgs_rgb[0].shape[:2],'depth')
      s2t_flow_p=self.reproj_helper(pct_reproj,flow,imgs_rgb[0].shape,'color')
      s2t_mask_p=(s2t_d_p!=0).astype('int')

      # compute an envelop box
      try:
        tp=np.where(t2s_d_p.sum(0))[0]
        w0,w1=tp[0],tp[-1]
        tp=np.where(t2s_d_p.sum(1))[0]
        h0,h1=tp[0],tp[-1]
      except:
        w0,h0=0,0
        w1,h1=t2s_d_p.shape[1]-1,t2s_d_p.shape[0]-1
      t2s_box_p = np.zeros(t2s_d_p.shape)
      t2s_box_p[h0:h1,w0:w1] = 1

      try:
        tp=np.where(s2t_d_p.sum(0))[0]
        w0,w1=tp[0],tp[-1]
        tp=np.where(s2t_d_p.sum(1))[0]
        h0,h1=tp[0],tp[-1]
      except:
        w0,h0=0,0
        w1,h1=s2t_d_p.shape[1]-1,s2t_d_p.shape[0]-1
      s2t_box_p = np.zeros(s2t_d_p.shape)
      s2t_box_p[h0:h1,w0:w1] = 1

      rets['proj_dr'] = np.stack((t2s_dr,s2t_dr),0)[np.newaxis,:]
      rets['proj_flow'] =np.stack((t2s_flow_p,s2t_flow_p),0).transpose(0,3,1,2)[np.newaxis,:]
      rets['proj_rgb'] =np.stack((t2s_rgb,s2t_rgb),0).transpose(0,3,1,2)[np.newaxis,:]
      rets['proj_rgb_p'] =np.stack((t2s_rgb_p,s2t_rgb_p),0).transpose(0,3,1,2)[np.newaxis,:]
      rets['proj_n_p']   =np.stack((t2s_n_p,s2t_n_p),0).transpose(0,3,1,2)[np.newaxis,:]
      rets['proj_d_p']   =np.stack((t2s_d_p,s2t_d_p),0).reshape(1,2,1,t2s_d_p.shape[0],t2s_d_p.shape[1])
      rets['proj_mask_p']=np.stack((t2s_mask_p,s2t_mask_p),0).reshape(1,2,1,t2s_mask_p.shape[0],t2s_mask_p.shape[1])
      rets['proj_box_p'] = np.stack((t2s_box_p,s2t_box_p),0).reshape(1,2,1,t2s_box_p.shape[0],t2s_box_p.shape[1])

    if self.segm:
      segm[0] = (self.LoadImage(os.path.join(basePath,'semanticLabel','{}.png'.format(frameid0)),depth=False)[:,:,0:1].copy()).transpose(2,0,1)
      segm[1] = (self.LoadImage(os.path.join(basePath,'semanticLabel','{}.png'.format(frameid1)),depth=False)[:,:,0:1].copy()).transpose(2,0,1)
      segm_ = np.zeros((self.nViews,1,*self.OutputSize[::-1]), dtype = np.float32)
      segm_[0] = segm[0]
      segm_[1] = segm[1]
      segm_ = segm_[np.newaxis,:]

    imgsPath.append(f"{basePath}/{ct0:06d}")
    imgsPath.append(f"{basePath}/{ct1:06d}")
    
    for v in range(self.nViews):
      imgs_[v] =  cv2.resize(imgs[v], self.OutputSize,interpolation=cv2.INTER_NEAREST)
      if self.rgbd:
        imgs_rgb_[v] =  cv2.resize(imgs_rgb[v], self.OutputSize).transpose(2,0,1)

    imgs_ = imgs_[np.newaxis,:]
    if self.hmap:
      hmap = hmap[np.newaxis,:]
    if self.rgbd:
      imgs_rgb_ = imgs_rgb_[np.newaxis,:]
    if self.birdview:
      imgs_bv_ = imgs_bv_[np.newaxis,:]
    if self.pointcloud:
      pointcloud = pointcloud[np.newaxis,:]
    R = R[np.newaxis,:]
    Q = Q[np.newaxis,:]

    if self.segm:
      rets['segm']=segm_
    rets['interval']=self.interval_this
    rets['norm']=normal_
    rets['rgb']=imgs_rgb_
    rets['depth']=imgs_
    rets['Q']=Q
    rets['R']=R
    rets['imgsPath']=imgsPath

    return rets
    
  def __len__(self):
    return self.len


