from torch.utils.data import Dataset
import torch
from pathlib import Path
import numpy as np
import pyexr
from PIL import Image
from torchvision.transforms import Compose, Normalize, Resize, ToTensor
import torchvision.transforms.functional as F

from data_processing.distance_to_depth import FromDistanceToDepth, get_intrinsic
from data_processing.volume_reader import read_df

class SquarePad:
	def __call__(self, image):
		w, h = image.size
		max_wh = np.max([w, h])
		hp = int((max_wh - w) / 2)
		vp = int((max_wh - h) / 2)
		padding = (hp, vp, hp, vp)
		return F.pad(image, padding, 0, 'constant')

class scene_net_data(Dataset):

    def __init__(self, split, dataset_path, num_points, splitsdir, kwargs=None):
        self.kwargs = kwargs
        self.dataset_path = Path(dataset_path)
        self.split = split
        self.splitsdir = splitsdir
        self.split_shapes = [x.strip() for x in (Path("data/splits") / splitsdir / f"{split}.txt").read_text().split("\n") if x.strip() != ""]
        self.data = [x for x in self.split_shapes]
        self.data = self.data * (50 if ('overfit' in splitsdir) and split == 'train' else 1)
        self.num_points = num_points
        print(split, dataset_path, splitsdir)
        resize_transfrom = Compose([SquarePad(), Resize((kwargs.W, kwargs.W))])

        if kwargs.resize_input:
            self.input_transform = Compose(
                [
                    resize_transfrom,
                    ToTensor(),
                    Normalize((0.5, 0.5, 0.5), (0.5, 0.5, 0.5)),
                ]
            )
        else:
            self.input_transform = Compose([ToTensor(),Normalize((0.5, 0.5, 0.5), (0.5, 0.5, 0.5))])
            
        self.target_transform = ToTensor()


    def __len__(self):
        return len(self.data)

    def __getitem__(self, idx):
        item = self.data[idx]
        sample_folder = Path(self.dataset_path) / "raw" / self.splitsdir / item
        df_folder = Path(self.dataset_path) / "processed" / self.splitsdir / item
        
        image = Image.open(sample_folder / "rgb.png")
        #image = image.transpose(Image.FLIP_LEFT_RIGHT)
        rgb_img = self.input_transform(image)

        points = []
        occupancies = []
        grids = []

        for sigma in ['0.10', '0.01']:
            sample_points_occ_npz = np.load(df_folder / f"occupancy_{sigma}.npz")
            boundary_sample_points = sample_points_occ_npz['points']
            boundary_sample_coords = sample_points_occ_npz['grid_coords']
            boundary_sample_occupancies = sample_points_occ_npz['occupancies']
            subsample_indices = np.random.randint(0, boundary_sample_points.shape[0], self.num_points)
            points.extend(boundary_sample_points[subsample_indices])
            grids.extend(boundary_sample_coords[subsample_indices])
            occupancies.extend(boundary_sample_occupancies[subsample_indices])

        sample_points = torch.from_numpy(np.array(points, dtype=f'float{self.kwargs.precision}'))  # * (1 - 16 / 64))
        sample_occupancies = torch.from_numpy(np.array(occupancies, dtype=f'float{self.kwargs.precision}'))

        distance_map = pyexr.open(str(sample_folder / "distance.exr")).get("R")[:, :, 0]

        #depthmap target
        intrinsics_matrix = get_intrinsic(Path("data/intrinsics.txt"))
        focal_length = intrinsics_matrix[0][0]
        transform = FromDistanceToDepth(focal_length)
        depth_map = transform(distance_map).numpy().astype(f'float{self.kwargs.precision}', casting='same_kind')
        #depth_map = np.flip(depth_map, 1)
        depth_flipped = depth_map.copy()
        depthmap_target = self.target_transform(depth_flipped)
        mesh_path = str(sample_folder / "mesh.obj")
        
        # GT mesh
        #df_folder = Path(self.dataset_path) / "processed" / self.splitsdir / item
        #sample_target = torch.from_numpy(read_df(str(df_folder / "distance_field.df")).astype(f'float{self.kwargs.precision}')).unsqueeze(0)
        
        return {
            'name': item,
            'mesh':mesh_path,
            'rgb': rgb_img,
            'points': sample_points,
            'occupancies': sample_occupancies,
            #'target': sample_target.unsqueeze(0),
            'depthmap_target': depthmap_target.squeeze()
        }