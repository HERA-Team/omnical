#!/usr/bin/env python

import aipy as ap
import numpy as np
import commands, os, time, math, ephem, optparse, sys
import omnical.calibration_omni as omni
import cPickle as pickle
import scipy.signal as ss
FILENAME = "omnical_PSA128.py"



######################################################################
##############Config parameters###################################
######################################################################
o = optparse.OptionParser()

ap.scripting.add_standard_options(o, cal=True, pol=True)
o.add_option('-t', '--tag', action = 'store', default = 'PSA128', help = 'tag name of this calibration')
o.add_option('-d', '--datatag', action = 'store', default = 'PSA128', help = 'tag name of this data set')
o.add_option('-i', '--infopath', action = 'store', default = '/data2/home/hz2ug/omnical/doc/redundantinfo_PSA128_17ba.bin', help = 'redundantinfo file to read')
o.add_option('-r', '--rawcalpath', action = 'store', default = 'NORAWCAL', help = 'raw calibration parameter file to read. The file should be a pickle file generated by first_cal.py')
o.add_option('--add', action = 'store_true', help = 'whether to enable crosstalk removal')
o.add_option('--nadd', action = 'store', type = 'int', default = -1, help = 'time steps w to remove additive term with. for running average its 2w + 1 sliding window.')
o.add_option('--flagsigma', action = 'store', type = 'float', default = 4, help = 'Number of sigmas to flag on chi^2 distribution. 4 sigma by default.')
o.add_option('--flagt', action = 'store', type = 'int', default = 4, help = 'Number of time slices to run the minimum filter when flagging. 4 by default.')
o.add_option('--flagf', action = 'store', type = 'int', default = 4, help = 'Number of frequency slices to run the minimum filter when flagging. 4 by default.')
o.add_option('--datapath', action = 'store', default = 'NOBINDATA', help = 'binary data file folder')
o.add_option('--healthbar', action = 'store', default = '2', help = 'health threshold (0-100) over which an antenna is marked bad.')
o.add_option('-o', '--outputpath', action = 'store', default = ".", help = 'output folder')
o.add_option('-k', '--skip', action = 'store_true', help = 'whether to skip data importing from uv')
o.add_option('-u', '--newuv', action = 'store_true', help = 'whether to create new uv files with calibration applied')
o.add_option('-f', '--overwrite', action = 'store_true', help = 'whether to overwrite if the new uv files already exists')
o.add_option('--plot', action = 'store_true', help = 'whether to make plots in the end')

opts,args = o.parse_args(sys.argv[1:])
skip = opts.skip
create_new_uvs = opts.newuv
overwrite_uvs = opts.overwrite
make_plots = opts.plot
ano = opts.tag##This is the file name difference for final calibration parameter result file. Result will be saved in miriadextract_xx_ano.omnical
dataano = opts.datatag#ano for existing data and lst.dat
sourcepath = opts.datapath
oppath = opts.outputpath
uvfiles = args
flag_thresh = opts.flagsigma
flagt = opts.flagt
flagf = opts.flagf

keep_binary_data = False
if os.path.isdir(sourcepath):
    keep_binary_data = True
elif opts.skip:
    raise IOError("Direct binary data import requested by -k or --skip option, but the --datapth %s doesn't exist."%sourcepath)

#print opts.healthbar, opts.healthbar.split(), len(opts.healthbar.split())
if len(opts.healthbar.split(',')) == 1:
    healthbar = float(opts.healthbar)
    ubl_healthbar = 100
elif len(opts.healthbar.split(',')) == 2:
    healthbar = float(opts.healthbar.split(',')[0])
    ubl_healthbar = float(opts.healthbar.split(',')[1])
else:
    raise Exception("User input healthbar option (--healthbar %s) is not recognized."%opts.healthbar)
for uvf in uvfiles:
    if not os.path.isdir(uvf):
        uvfiles.remove(uvf)
        print "WARNING: uv file path %s does not exist!"%uvf
if len(uvfiles) == 0:
    raise Exception("ERROR: No valid uv files detected in input. Exiting!")

wantpols = {}
for p in opts.pol.split(','): wantpols[p] = ap.miriad.str2pol[p]
#wantpols = {'xx':ap.miriad.str2pol['xx']}#, 'yy':-6}#todo:

print "Reading calfile %s"%opts.cal,
sys.stdout.flush()
aa = ap.cal.get_aa(opts.cal, np.array([.15]))
print "Done"
sys.stdout.flush()

infopaths = {}
for pol in wantpols.keys():
    infopaths[pol]= opts.infopath


removedegen = True
if opts.add and opts.nadd > 0:
    removeadditive = True
    removeadditiveperiod = opts.nadd
else:
    removeadditive = False
    removeadditiveperiod = -1

crudecalpath = opts.rawcalpath
needrawcal = False
if os.path.isfile(crudecalpath):
    needrawcal = True
    with open(crudecalpath, 'rb') as crude_calpar_file:
        crude_calpar = pickle.load(crude_calpar_file)
elif crudecalpath != 'NORAWCAL':
    raise IOError("Input rawcalpath %s doesn't exist on disk."%crudecalpath)


keep_binary_calpar = True

converge_percent = 0.001
max_iter = 20
step_size = .3

######################################################################
######################################################################
######################################################################

########Massage user parameters###################################
sourcepath += '/'
oppath += '/'
utcPath = sourcepath + 'miriadextract_' + dataano + "_localtime.dat"
lstPath = sourcepath + 'miriadextract_' + dataano + "_lsthour.dat"

####get some info from the first uvfile   ################
print "Getting some basic info from %s"%uvfiles[0],
sys.stdout.flush()
uv=ap.miriad.UV(uvfiles[0])
nfreq = uv.nchan;
nant = uv['nants']
sa = ephem.Observer()
sa.lon = uv['longitu']
sa.lat = uv['latitud']
#startfreq = uv['sfreq']
#dfreq = uv['sdf']
del(uv)
print "Done."
sys.stdout.flush()




###start reading miriads################
if skip:
    print FILENAME + " MSG: SKIPPED reading uvfiles. Reading binary data files directly...",
    sys.stdout.flush()
    with open(utcPath) as f:
        timing = f.readlines()
        timing = [t.replace('\n','') for t in timing]
    print (len(timing), nfreq, len(aa) * (len(aa) + 1) / 2), "...",
    data = np.array([np.fromfile(sourcepath + 'data_' + dataano + '_' + pol, dtype = 'complex64').reshape((len(timing), nfreq, len(aa) * (len(aa) + 1) / 2)) for pol in wantpols.keys()])
    print "Done."
    sys.stdout.flush()

else:
    print FILENAME + " MSG:",  len(uvfiles), "uv files to be processed for " + ano
    sys.stdout.flush()
    data, t, timing, lst = omni.importuvs(uvfiles, np.concatenate([[[i,j] for i in range(j + 1)] for j in range(len(aa))]), wantpols, timingTolerance=100)#, nTotalAntenna = len(aa))
    print FILENAME + " MSG:",  len(t), "slices read. data shape: ", data.shape
    sys.stdout.flush()

    if keep_binary_data:
        print FILENAME + " MSG: saving binary data to disk...",
        sys.stdout.flush()
        f = open(utcPath,'w')
        for qaz in timing:
            f.write("%s\n"%qaz)
        f.close()
        f = open(lstPath,'w')
        for l in lst:
            f.write("%s\n"%l)
        f.close()
        for p,pol in zip(range(len(wantpols)), wantpols.keys()):
            data[p].tofile(sourcepath + 'data_' + dataano + '_' + pol)
        print "Done."
        sys.stdout.flush()
sun = ephem.Sun()
sunpos  = np.zeros((len(timing), 2))
cenA = ephem.FixedBody()
cenA._ra = 3.5146
cenA._dec = -.75077
cenApos = np.zeros((len(timing), 2))
for nt,tm in zip(range(len(timing)),timing):
    sa.date = tm

    sun.compute(sa)
    sunpos[nt] = sun.alt, sun.az
    cenA.compute(sa)
    cenApos[nt] = cenA.alt, cenA.az
print FILENAME + " MSG: data time range UTC: %s to %s, sun altaz from (%f,%f) to (%f,%f)"%(timing[0], timing[-1], sunpos[0,0], sunpos[0,1], sunpos[-1,0], sunpos[-1,1])#, "CentaurusA altaz from (%f,%f) to (%f,%f)"%(cenApos[0,0], cenApos[0,1], cenApos[-1,0], cenApos[-1,1])
sys.stdout.flush()
####create redundant calibrators################
calibrators = {}
omnigains = {}
adds = {}
flags = {}
for p, pol in zip(range(len(data)), wantpols.keys()):

    calibrators[pol] = omni.RedundantCalibrator_PAPER(aa)
    calibrators[pol].read_redundantinfo(infopaths[pol], verbose=False)
    info = calibrators[pol].Info.get_info()
    calibrators[pol].nTime = len(timing)
    calibrators[pol].nFrequency = nfreq

    ###apply, if needed, raw calibration################
    if needrawcal:
        original_data = np.copy(data[p])
        data[p] = omni.apply_calpar(data[p], crude_calpar[pol], calibrators[pol].totalVisibilityId)

    ####calibrate################
    calibrators[pol].removeDegeneracy = removedegen
    calibrators[pol].convergePercent = converge_percent
    calibrators[pol].maxIteration = max_iter
    calibrators[pol].stepSize = step_size

    ################first round of calibration  #########################
    print FILENAME + " MSG: starting calibration on %s %s. nTime = %i, nFrequency = %i ..."%(dataano, pol, calibrators[pol].nTime, calibrators[pol].nFrequency),
    sys.stdout.flush()
    timer = time.time()
    additivein = np.zeros_like(data[p])

    calibrators[pol].logcal(data[p], additivein, verbose=True)

    if needrawcal:#restore original data after logcal
        calibrators[pol].rawCalpar[:, :, 3:3 + calibrators[pol].nAntenna] = calibrators[pol].rawCalpar[:, :, 3:3 + calibrators[pol].nAntenna] + np.log10(np.abs(crude_calpar[pol][:, calibrators[pol].subsetant]))
        calibrators[pol].rawCalpar[:, :, 3 + calibrators[pol].nAntenna:3 + 2 * calibrators[pol].nAntenna] = calibrators[pol].rawCalpar[:, :, 3 + calibrators[pol].nAntenna:3 + 2 * calibrators[pol].nAntenna] + np.angle(crude_calpar[pol][:, calibrators[pol].subsetant])
        data[p] = np.copy(original_data)
        del original_data

    additiveout = calibrators[pol].lincal(data[p], additivein, verbose=True)
    #######################remove additive###############################
    if removeadditive:
        nadditiveloop = 1
        for i in range(nadditiveloop):
            additivein[:,:,calibrators[pol].Info.subsetbl] = additivein[:,:,calibrators[pol].Info.subsetbl] + additiveout
            weight = ss.convolve(np.ones(additivein.shape[0]), np.ones(removeadditiveperiod * 2 + 1), mode='same')
            for f in range(additivein.shape[1]):#doing for loop to save memory usage at the expense of negligible time
                additivein[:,f] = ss.convolve(additivein[:,f], np.ones(removeadditiveperiod * 2 + 1)[:, None], mode='same')/weight[:, None]
            calibrators[pol].computeUBLFit = False
            additiveout = calibrators[pol].lincal(data[p], additivein, verbose=True)

    #####################flag bad data according to chisq#########################
    flags[pol] = calibrators[pol].flag(nsigma = flag_thresh, twindow=flagt, fwindow=flagf)

    print "Done. %fmin"%(float(time.time()-timer)/60.)
    sys.stdout.flush()
    #######################save results###############################
    calibrators[pol].utctimes = timing
    omnigains[pol] = calibrators[pol].get_omnigain()
    adds[pol] = additivein
    if keep_binary_calpar:
        print FILENAME + " MSG: saving calibration results on %s %s."%(dataano, pol),
        sys.stdout.flush()
        #Zaki: catch these outputs and save them to wherever you like
        calibrators[pol].rawCalpar.tofile(oppath + '/' + dataano + '_' + ano + "_%s.omnical"%pol)
        if removeadditive:
            adds[pol].tofile(oppath + '/' + dataano + '_' + ano + "_%s.omniadd"%pol + str(removeadditiveperiod))
        #calibrators[pol].get_calibrated_data(data[p])
        #calibrators[pol].get_omnichisq()
        #calibrators[pol].get_omnifit()
        print "Done"
        sys.stdout.flush()
    diag_txt = calibrators[pol].diagnose(data = data[p], additiveout = additiveout, healthbar = healthbar, ubl_healthbar = ubl_healthbar, ouput_txt = True)
    text_file = open(oppath + '/' + dataano + '_' + ano + "_%s.diagtxt"%pol, "a")
    text_file.write(diag_txt)
    text_file.close()

if create_new_uvs:
    print FILENAME + " MSG: saving new uv files",
    sys.stdout.flush()
    infos = {}
    for pol in wantpols.keys():
        infos[pol] = omni.read_redundantinfo(infopaths[pol])
    omni.apply_omnigain_uvs(uvfiles, omnigains, calibrators[wantpols.keys()[0]].totalVisibilityId, infos, wantpols, oppath, ano, adds= adds, verbose = True, comment = '_'.join(sys.argv), overwrite = overwrite_uvs)
    print "Done"
    sys.stdout.flush()
if make_plots:
    import matplotlib.pyplot as plt
    for p,pol in zip(range(len(wantpols)), wantpols.keys()):
        plt.subplot(2, len(wantpols), 2 * p + 1)
        plot_data = (calibrators[pol].rawCalpar[:,:,2]/(len(calibrators[pol].Info.subsetbl)-calibrators[pol].Info.nAntenna - calibrators[pol].Info.nUBL))**.5
        plt.imshow(plot_data, vmin = 0, vmax = (np.nanmax(calibrators[wantpols.keys()[0]].rawCalpar[:,:,2][flags[wantpols.keys()[0]]])/(len(calibrators[pol].Info.subsetbl)-calibrators[pol].Info.nAntenna - calibrators[pol].Info.nUBL))**.5, interpolation='nearest')
        plt.title('RMS fitting error per baseline')
        plt.colorbar()

        plt.subplot(2, len(wantpols), 2 * p + 2)
        flag_plot_data = np.copy(plot_data)
        flag_plot_data[~flags[pol]] = 0
        plt.imshow(flag_plot_data, vmin = 0, vmax = (np.nanmax(calibrators[wantpols.keys()[0]].rawCalpar[:,:,2][flags[wantpols.keys()[0]]])/(len(calibrators[pol].Info.subsetbl)-calibrators[pol].Info.nAntenna - calibrators[pol].Info.nUBL))**.5, interpolation='nearest')
        plt.title('flagged RMS fitting error per baseline')
        plt.colorbar()
    plt.show()
