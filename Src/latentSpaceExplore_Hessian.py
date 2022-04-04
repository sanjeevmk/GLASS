import torch
torch.manual_seed(10)
torch.cuda.manual_seed_all(10)
import os
import numpy as np
from sklearn.metrics.pairwise import cosine_similarity as cs

import Energies
import Leapfrog
import Hessian 
from Utils import writer,Off,Obj,Pts,rect_remesh
import header 
from torch.autograd import Variable
import math
from RegArap import ARAP as ArapReg

def get_jacobian_rand(cur_shape, z, model, epsilon=[1e-3], nz_max=10):
    nb, nz = z.size()
    _, n_vert, nc = cur_shape.size()
    if nz >= nz_max:
      rand_idx = np.random.permutation(nz)[:nz_max]
      nz = nz_max
    else:
      rand_idx = np.arange(nz)
    
    jacobian = torch.zeros((nb, n_vert*nc, nz)).cuda()
    jacobian.requires_grad_(False)
    for i, idx in enumerate(rand_idx):
        dz = torch.zeros(z.size()).cuda()
        dz[:, idx] = epsilon
        z_new = z + dz
        shape_new = model(z_new)
        dout = (shape_new - cur_shape).view(nb, -1)
        jacobian[:, :, i] = dout/epsilon
    return jacobian

def hessianExplore(training_params,data_classes,network_params,losses,misc_variables,rounds):
    for parameter in network_params.autoEncoder.decoder1.parameters():
        parameter.requires_grad = False
    shapes = []
    meshNames = []
    for ind,data in enumerate(data_classes.expanded):
        pts,faces,numNeighbors,accnumNeighbors,neighborsMatrix,weightMatrix,area,names,_,_ = data
        pts = torch.unsqueeze(torch.from_numpy(pts).float().cuda(),0)
        pts = pts[:,:,:3]
        meshNames.extend(names)
        shapes.extend(pts)

    _,template_face,_,_,_,_,_,_,_,_ = data_classes.original[0]
    arapreg = ArapReg(template_face,network_params.numPoints).cuda()
    names = meshNames[0]

    print("Round:",rounds," HMC",flush=True)
    network_params.autoEncoder.eval()
    network_params.autoEncoder.load_state_dict(torch.load(network_params.weightPath+"_r"+str(rounds)))

    if not os.path.exists(misc_variables.sampleDir):
        os.makedirs(misc_variables.sampleDir)

    def dumpSamples(outDir,samples,p_pts=None,corner=-1):
        for i in range(len(samples)):
            objInstance = Obj.Obj("dummy.obj")
            '''
            newPoints,newFaces,corner_vid = rect_remesh.rectRemesh(samples[i],faces,corner_vid=corner)
            objInstance.setCorner(corner_vid)
            '''
            objInstance.setVertices(samples[i])
            objInstance.setFaces(faces)
            objInstance.saveAs(os.path.join(outDir,str(rounds) + "_" + names.split(".")[0] + "_" + str(i)+".obj"))

            if p_pts is not None:
                ptsInstance = Pts.Pts("dummy.pts")
                ptsInstance.setVertices(p_pts)
                ptsInstance.saveAs(os.path.join(outDir,str(rounds) + "_" + names.split(".")[0] + "_" + str(i)+".pts"))

    hessian = Hessian.Hessian(training_params.numsteps,0.1)
    for ind,data in enumerate(data_classes.expanded):
        network_params.autoEncoder.eval()
        samples = []
        energies = []
        print("HMC Source Data:",ind,len(data_classes.expanded),flush=True)
        pts,faces,numNeighbors,accnumNeighbors,neighborsMatrix,weightMatrix,area,names,original_parent,_ = data
        print(pts.shape,names)
        query = np.expand_dims(pts[:,:3],0)
        query = np.reshape(query,(1,-1))
        pts = torch.unsqueeze(torch.from_numpy(pts).float().cuda(),0)
        pts = pts[:,:,:3]

        p_pts = torch.unsqueeze(torch.from_numpy(original_parent).float().cuda(),0)
        p_pts = p_pts[:,:,:3]
        numNeighbors = torch.unsqueeze(torch.from_numpy(numNeighbors).int().cuda(),0)
        accnumNeighbors = torch.unsqueeze(torch.from_numpy(accnumNeighbors).int().cuda(),0)
        neighborsMatrix = torch.unsqueeze(torch.from_numpy(neighborsMatrix).int().cuda(),0)
        weightMatrix = torch.unsqueeze(torch.from_numpy(weightMatrix).float().cuda(),0)
        area = torch.unsqueeze(torch.from_numpy(area).float().cuda(),0)

        network_params.autoEncoder.zero_grad()

        energy_fn = None
        if training_params.energy=='pdist':
            energy_fn = Energies.NLLPairwiseDistanceEnergy(p_pts,network_params.testEnergyWeight)
        if training_params.energy=='arap':
            #energy_fn = Energies.ArapEnergy(pts,neighborsMatrix,numNeighbors,accnumNeighbors,weightMatrix,network_params.testEnergyWeight)
            energy_fn = Energies.ArapEnergyHessian(pts[:,:,:3],neighborsMatrix,numNeighbors,accnumNeighbors,weightMatrix,network_params.testEnergyWeight)
        if training_params.energy=='regarap':
            energy_fn = Energies.ArapRegHessian(template_face,network_params.numPoints,nz_max=network_params.bottleneck)
        if training_params.energy=='iso':
            energy_fn = Energies.IsometricEnergy(pts,neighborsMatrix,numNeighbors,network_params.testEnergyWeight)
        if training_params.energy=='arap2d':
            energy_fn = Energies.ArapEnergy2D(pts,neighborsMatrix,numNeighbors,weightMatrix,network_params.testEnergyWeight)
        if training_params.energy=='asap':
            energy_fn = Energies.AsapEnergy(pts,neighborsMatrix,numNeighbors,weightMatrix,network_params.testEnergyWeight)
        if training_params.energy=='asap2d':
            energy_fn = Energies.AsapEnergy2D(pts,neighborsMatrix,numNeighbors,weightMatrix,network_params.testEnergyWeight)
        if training_params.energy=='carap':
            energy_fn = Energies.CArapEnergy(pts,neighborsMatrix,numNeighbors,weightMatrix,misc_variables.alpha,area,network_params.testEnergyWeight)
        #seedCode = network_params.autoEncoder.encoder(torch.unsqueeze(pts,1))
        seedCode = network_params.autoEncoder.encoder(pts.transpose(2,1))

        recon = network_params.autoEncoder.decoder1(seedCode)
        #recon = network_params.autoEncoder.decoder2(pts,recon,neighborsMatrix,numNeighbors,weightMatrix,network_params.testEnergyWeight)
        objInstance = Obj.Obj("dummy.obj")
        objInstance.setVertices(recon.cpu().detach().numpy()[0])
        objInstance.setFaces(faces)
        objInstance.saveAs(os.path.join(misc_variables.reconDir,str(rounds) + "_" + names.split(".")[0] + "_recon.obj"))

        stepSize = training_params.stepsize
        stepsizeMin = 0.001
        stepsizeMax = 0.1
        stepsizeInc = 1.02
        stepsizeDec = 0.98
        targetAcceptanceRate = 0.7
        avgAcceptanceSlowness = 0.9
        avgAcceptance = 0.0
        burnin = 0

        code = seedCode.clone().detach()

        from datetime import datetime
        import time
        start = datetime.now()
        for i in range(training_params.hmcEpochs):
            newCode,energy = hessian(code,energy_fn,stepSize=stepSize,k=training_params.ncomp,decoder=network_params.autoEncoder)

            newSample = network_params.autoEncoder.decoder1(newCode)
            #newSample = network_params.autoEncoder.decoder2(pts[:,:,:3],newSample,neighborsMatrix,numNeighbors,accnumNeighbors,weightMatrix,network_params.testEnergyWeight)
            network_params.autoEncoder.zero_grad()
            code = seedCode.clone().detach()
            if network_params.dims == 2:
                z = torch.zeros(1,network_params.numPoints,1).float().cuda()
                newSample = torch.cat((newSample,z),2)

            if np.isnan(newSample.cpu().detach().numpy()[0]).any():
                continue
            energies.append(energy/network_params.testEnergyWeight.item())
            samples.append(newSample.cpu().detach().numpy()[0])

        end = datetime.now()
        print("HMC Time:",(end-start).seconds)
        if len(energies) == 0:
            continue
        print("min energy:",min(energies))

        if math.isnan(energies[0].item()):
            continue

        if len(samples)==0:
            continue

        start = datetime.now()

        selectedSamples = np.expand_dims(np.array(samples[-1]),0)

        selectedSamples = np.array([s.cpu().detach().numpy() for i,s in enumerate(shapes) if meshNames[i]!=names])
        selectedSamples = np.reshape(selectedSamples,(selectedSamples.shape[0],-1))
        initialLen = len(selectedSamples)
        samples = np.array(samples)
        mmr_lambda=0.3
        print("Round:",rounds,len(samples)," SAMPLE SELECTION")
        if len(samples)>0:
            for numAddedSamples in range(misc_variables.numNewSamples):
                print("sample adding:",numAddedSamples,misc_variables.numNewSamples)
                allDistances = []
                for s in samples:
                    flatS = np.reshape(s[:,:3],(1,-1))
                    QDist = np.mean((query- flatS)**2,1)[0]
                    SSimilarity = np.max(cs(selectedSamples,flatS))
                    QSimilarity = np.max(cs(query,flatS))
                    similarity = mmr_lambda*QSimilarity - (1-mmr_lambda)*SSimilarity
                    allDistances.append(similarity)

                allDistances = np.array(allDistances)
                index = np.argmax(allDistances)
                print(index,len(energies),allDistances.shape)
                selectedS = samples[index,:,:]
                print("selected energy:",energies[index])
                samples = np.delete(samples,index,axis=0)
                selectedSamples = np.vstack((selectedSamples,np.reshape(selectedS[:,:3],(1,-1))))

        selectedSamples = selectedSamples[initialLen:,:]
        selectedSamples = np.reshape(selectedSamples,(selectedSamples.shape[0],network_params.numPoints,3))
        end = datetime.now()
        print("MMR Time:",(end-start).seconds)

        if network_params.dims==2:
            z = np.zeros((selectedSamples.shape[0],network_params.numPoints,1))
            selectedSamples = np.concatenate((selectedSamples,z),2)

        if training_params.project:
            dumpSamples(misc_variables.hmcsampleDir,selectedSamples)

            bestShape = None
            perturbedShape = np.array(selectedSamples)[:,:,:3]
            perturbedShape = torch.from_numpy(perturbedShape).float().cuda()
            proposedShape = header.Position(perturbedShape.view(1,-1))
            for parameter in network_params.autoEncoder.encoder.parameters():
                parameter.requires_grad = False
            codeOptimizer = torch.optim.Adam(proposedShape.parameters(),lr=1e-3,weight_decay=0 ) #1e-4)

            bestEnergy = 1e9
            prevEnergy = 1e9
            for i in range(training_params.optEpochs):
                codeOptimizer.zero_grad()

                energy = None
                if training_params.energy == 'pdist':
                    energy = losses.pdist(p_pts,proposedShape.proposed.view(1,network_params.numPoints,network_params.dims),network_params.testEnergyWeight)
                if training_params.energy == 'arap':
                    energy,_ = losses.arap(pts[:,:,:3],proposedShape.proposed.view(1,network_params.numPoints,3),neighborsMatrix,numNeighbors,accnumNeighbors,weightMatrix,network_params.testEnergyWeight)
                    energy = energy.mean()
                if training_params.energy == 'regarap':
                    code = network_params.autoEncoder.encoder(proposedShape.proposed.view(1,network_params.numPoints,3).transpose(2,1))
                    code = code.detach()
                    jacob = get_jacobian_rand(proposedShape.proposed.view(1,network_params.numPoints,3), code,network_params.autoEncoder.decoder1,epsilon=1e-1,nz_max=network_params.bottleneck)
                    energy,_ = arapreg(proposedShape.proposed.view(1,network_params.numPoints,3), jacob,)
                    energy /= jacob.shape[-1]
                    print(energy)
                if training_params.energy == 'iso':
                    energy = losses.isometric(pts,proposedShape.proposed.view(1,network_params.numPoints,network_params.dims),neighborsMatrix,numNeighbors,network_params.testEnergyWeight).mean()
                if training_params.energy == 'arap2d':
                    energy = losses.arap2d(pts,proposedShape.proposed.view(1,network_params.numPoints,network_params.dims),neighborsMatrix,numNeighbors,weightMatrix,network_params.testEnergyWeight).mean()
                if training_params.energy == 'asap':
                    energy = losses.asap(pts,proposedShape.proposed.view(1,network_params.numPoints,network_params.dims),neighborsMatrix,numNeighbors,weightMatrix,network_params.testEnergyWeight).mean()
                if training_params.energy == 'asap2d':
                    energy = losses.asap2d(pts,proposedShape.proposed.view(1,network_params.numPoints,network_params.dims),neighborsMatrix,numNeighbors,weightMatrix,network_params.testEnergyWeight).mean()
                if training_params.energy == 'carap':
                    energy = losses.carap(pts,proposedShape.proposed.view(1,network_params.numPoints,network_params.dims),neighborsMatrix,numNeighbors,weightMatrix,misc_variables.alpha,area,network_params.testEnergyWeight).mean()

                #print(energy.item())
                energy.backward()
                codeOptimizer.step()
                if energy.item() < bestEnergy:
                    bestEnergy = energy.item()/network_params.testEnergyWeight.item()
                    bestShape = proposedShape.proposed.view(1,network_params.numPoints,3)

                if bestEnergy < training_params.bestenergy:
                    break
                
                prevEnergy = energy.item()
            for parameter in network_params.autoEncoder.encoder.parameters():
                parameter.requires_grad = True
            print("perturbed",i,bestEnergy,flush=True)
            if bestShape is None:
                continue
            selectedSamples = bestShape.cpu().detach().numpy()

            #if bestEnergy>training_params.bestenergy:
            #    continue

            if network_params.dims==2:
                z = np.zeros((selectedSamples.shape[0],network_params.numPoints,1))
                selectedSamples = np.concatenate((selectedSamples,z),2)
            dumpSamples(misc_variables.sampleDir,selectedSamples,p_pts=original_parent)
        else:
            dumpSamples(misc_variables.sampleDir,selectedSamples,p_pts=original_parent)

    for parameter in network_params.autoEncoder.decoder1.parameters():
        parameter.requires_grad = False
    network_params.autoEncoder.train()
