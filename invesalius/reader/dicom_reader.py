#--------------------------------------------------------------------------
# Software:     InVesalius - Software de Reconstrucao 3D de Imagens Medicas
# Copyright:    (C) 2001  Centro de Pesquisas Renato Archer
# Homepage:     http://www.softwarepublico.gov.br
# Contact:      invesalius@cti.gov.br
# License:      GNU - GPL 2 (LICENSE.txt/LICENCA.txt)
#--------------------------------------------------------------------------
#    Este programa e software livre; voce pode redistribui-lo e/ou
#    modifica-lo sob os termos da Licenca Publica Geral GNU, conforme
#    publicada pela Free Software Foundation; de acordo com a versao 2
#    da Licenca.
#
#    Este programa eh distribuido na expectativa de ser util, mas SEM
#    QUALQUER GARANTIA; sem mesmo a garantia implicita de
#    COMERCIALIZACAO ou de ADEQUACAO A QUALQUER PROPOSITO EM
#    PARTICULAR. Consulte a Licenca Publica Geral GNU para obter mais
#    detalhes.
#--------------------------------------------------------------------------
import os
import Queue
import threading

from multiprocessing import cpu_count

import vtk
import gdcm
import wx.lib.pubsub as ps

import constants as const
import dicom
import dicom_grouper
import session

import glob
import utils


import plistlib

def ReadDicomGroup(dir_):

    patient_group = GetDicomGroups(dir_)
    if len(patient_group) > 0:
        filelist, dicom, zspacing = SelectLargerDicomGroup(patient_group)
        filelist = SortFiles(filelist, dicom)
        size = dicom.image.size
        bits = dicom.image.bits_allocad

        imagedata = CreateImageData(filelist, zspacing, size, bits)
        session.Session().project_status = const.NEW_PROJECT
        return imagedata, dicom
    else:
        return False


def SelectLargerDicomGroup(patient_group):
    maxslices = 0
    for patient in patient_group:
        group_list = patient.GetGroups()
        for group in group_list:
            if group.nslices > maxslices:
                maxslices = group.nslices
                larger_group = group

    return larger_group

def SortFiles(filelist, dicom):
    # Sort slices
    # FIXME: Coronal Crash. necessary verify
    if (dicom.image.orientation_label <> "CORONAL"):
        #Organize reversed image
        sorter = gdcm.IPPSorter()
        sorter.SetComputeZSpacing(True)
        sorter.SetZSpacingTolerance(1e-10)
        sorter.Sort(filelist)

        #Getting organized image
        filelist = sorter.GetFilenames()

    return filelist

tag_labels = {}
main_dict = {}
dict_file = {}

class LoadDicom(threading.Thread):
    
    def __init__(self, grouper, q, l):
        threading.Thread.__init__(self)
        self.grouper = grouper
        self.q = q
        self.l = l
    
    def run(self):

        grouper = self.grouper
        q = self.q
        
        while 1:
            
            filepath = q.get()
            if not filepath:
                break
            
            reader = gdcm.Reader()
            reader.SetFileName(filepath)
            
            if (reader.Read()):
                
                file = reader.GetFile()
            
                # Retrieve data set
                dataSet = file.GetDataSet()
            
                # Retrieve header
                header = file.GetHeader()
                stf = gdcm.StringFilter()

                field_dict = {}
                data_dict = {}


                tag = gdcm.Tag(0x0008, 0x0005)
                ds = reader.GetFile().GetDataSet()
                if ds.FindDataElement(tag):
                    encoding = str(ds.GetDataElement(tag).GetValue())
                    if not(encoding != None and encoding != "None" and encoding != "Loaded"):
                        encoding = "ISO_IR 100"
                else:
                    encoding = "ISO_IR_100" 
                # Iterate through the Header
                iterator = header.GetDES().begin()
                while (not iterator.equal(header.GetDES().end())):
                    dataElement = iterator.next()
                    stf.SetFile(file)
                    tag = dataElement.GetTag()
                    data = stf.ToStringPair(tag)
                    stag = tag.PrintAsPipeSeparatedString()
                    
                    group = stag.split("|")[0][1:]
                    field = stag.split("|")[1][:-1]
                    tag_labels[stag] = data[0]
                    
                    if not group in data_dict.keys():
                        data_dict[group] = {}

                    if not(utils.VerifyInvalidPListCharacter(data[1])):
                        data_dict[group][field] = data[1].decode(encoding)
                    else:
                        data_dict[group][field] = "Invalid Character"

                
                # Iterate through the Data set
                iterator = dataSet.GetDES().begin()
                while (not iterator.equal(dataSet.GetDES().end())):
                    dataElement = iterator.next()
                    
                    stf.SetFile(file)
                    tag = dataElement.GetTag()
                    data = stf.ToStringPair(tag)
                    stag = tag.PrintAsPipeSeparatedString()

                    group = stag.split("|")[0][1:]
                    field = stag.split("|")[1][:-1]
                    tag_labels[stag] = data[0]

                    if not group in data_dict.keys():
                        data_dict[group] = {}

                    if not(utils.VerifyInvalidPListCharacter(data[1])):
                        data_dict[group][field] = data[1].decode(encoding)
                    else:
                        data_dict[group][field] = "Invalid Character"
                

                
                # ----------   Refactory --------------------------------------
                data_dict['invesalius'] = {'orientation_label' : GetImageOrientationLabel(filepath)}

                # -------------------------------------------------------------
                dict_file[filepath] = data_dict
                
                #----------  Verify is DICOMDir -------------------------------
                is_dicom_dir = 1
                try: 
                    if (data_dict['0002']['0002'] != "1.2.840.10008.1.3.10"): #DICOMDIR
                        is_dicom_dir = 0
                except(KeyError):
                        is_dicom_dir = 0
                                            
                if not(is_dicom_dir):
                    parser = dicom.Parser()
                    parser.SetDataImage(dict_file[filepath], filepath)
                    
                    dcm = dicom.Dicom()
                    self.l.acquire()
                    dcm.SetParser(parser)
                    grouper.AddFile(dcm)

                    self.l.release()
                
                #==========  used in test =======================================
                #main_dict = dict(
                #                data  = dict_file,
                #                labels  = tag_labels)
           
                #plistlib.writePlist(main_dict, ".//teste.plist")"""


def GetImageOrientationLabel(filename):
    """
    Return Label regarding the orientation of
    an image. (AXIAL, SAGITTAL, CORONAL,
    OBLIQUE or UNKNOWN)
    """
    gdcm_reader = gdcm.ImageReader()
    gdcm_reader.SetFileName(filename)

    img = gdcm_reader.GetImage()
    direc_cosines = img.GetDirectionCosines()
    orientation = gdcm.Orientation()
    try:
        type = orientation.GetType(tuple(direc_cosines))
    except TypeError:
        type = orientation.GetType(direc_cosines)
    label = orientation.GetLabel(type)

    if (label):
        return label
    else:
        return ""


def yGetDicomGroups(directory, recursive=True, gui=True):
    """
    Return all full paths to DICOM files inside given directory.
    """
    nfiles = 0
    # Find total number of files
    if recursive:
        for dirpath, dirnames, filenames in os.walk(directory):
            nfiles += len(filenames)
    else:
        dirpath, dirnames, filenames = os.walk(directory)
        nfiles = len(filenames)

    counter = 0
    grouper = dicom_grouper.DicomPatientGrouper() 
    q = Queue.Queue()
    l = threading.Lock()
    threads = []
    for i in xrange(cpu_count()):
        t = LoadDicom(grouper, q, l)
        t.start()
        threads.append(t)
    # Retrieve only DICOM files, splited into groups
    if recursive:
        for dirpath, dirnames, filenames in os.walk(directory):
            for name in filenames:
                filepath = os.path.join(dirpath, name)
                counter += 1
                if gui:
                    yield (counter,nfiles)
                q.put(filepath)
    else:
        dirpath, dirnames, filenames = os.walk(directory)
        for name in filenames:
            filepath = str(os.path.join(dirpath, name))
            counter += 1
            if gui:
                yield (counter,nfiles)
            q.put(filepath)

    for t in threads:
        q.put(0)

    for t in threads:
        t.join()

    #TODO: Is this commented update necessary?
    #grouper.Update()
    yield grouper.GetPatientsGroups()


def GetDicomGroups(directory, recursive=True):
    return yGetDicomGroups(directory, recursive, gui=False).next()


class ProgressDicomReader:
    def __init__(self):
        ps.Publisher().subscribe(self.CancelLoad, "Cancel DICOM load")

    def CancelLoad(self, evt_pubsub):
        self.running = False
        self.stoped = True

    def SetWindowEvent(self, frame):
        self.frame = frame          

    def SetDirectoryPath(self, path,recursive=True):
        self.running = True
        self.stoped = False
        self.GetDicomGroups(path,recursive)

    def UpdateLoadFileProgress(self,cont_progress):
        ps.Publisher().sendMessage("Update dicom load", cont_progress)

    def EndLoadFile(self, patient_list):
        ps.Publisher().sendMessage("End dicom load", patient_list)

    def GetDicomGroups(self, path, recursive):

        if not const.VTK_WARNING:
            log_path = os.path.join(const.LOG_FOLDER, 'vtkoutput.txt')
            fow = vtk.vtkFileOutputWindow()
            fow.SetFileName(log_path)
            ow = vtk.vtkOutputWindow()
            ow.SetInstance(fow)
        print "=====>>> Progress... dicom_reader.py 367"
        
 
        y = yGetDicomGroups(path, recursive)
        for value_progress in y:
            if not self.running:
                break
            if isinstance(value_progress, tuple):
                self.UpdateLoadFileProgress(value_progress)
            else:
                self.EndLoadFile(value_progress)

        #Is necessary in the case user cancel
        #the load, ensure that dicomdialog is closed
        if(self.stoped):
            self.UpdateLoadFileProgress(None)
            self.stoped = False   

