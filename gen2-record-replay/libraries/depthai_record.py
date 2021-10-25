#!/usr/bin/env python3
from contextlib import ExitStack
from pathlib import Path
from multiprocessing import Array, Process, Queue
from cv2 import VideoWriter, VideoWriter_fourcc
import types
import os
import contextlib
import depthai as dai

def store_frames(path: Path, frame_q: Queue, stack: ExitStack) -> None:
    files = {}

    def create_video_file(name):
        file_path = str(path / f"{name}.mjpeg")
        # fourcc = VideoWriter_fourcc(*'MJPG')
        # width = self.nodes[name].getResolutionWidth()
        # height = self.nodes[name].getResolutionHeight()
        # writer = VideoWriter(path, fourcc, self.fps, (width, height))
        # writer.release()
        # time.sleep(0.001)
        files[name] = stack.enter_context(open(file_path, 'wb'))

    while True:
        try:
            frames = frame_q.get()
            if frames is None:
                break
            for name in frames:

                if name not in files: # File wasn't created yet
                    create_video_file(name)

                files[name].write(frames[name])
                # frames[name].tofile(files[name])
        except KeyboardInterrupt:
            break

    print('Exiting store frame process')

class Record:
    def __init__(self, path: str, device: dai.Device, stack: ExitStack) -> None:
        self.save = ['color', 'mono']
        self.fps = 30
        self.device = device

        self.stereo = 1 < len(device.getConnectedCameras())
        self.path = self.create_folder(path, device.getMxId())

        calibData = device.readCalibration()
        calibData.eepromToJsonFile(str(self.path / "calib.json"))

        self.convert_mp4 = False
        self.exit_stack = stack

    def start_recording(self) -> None:
        if not self.stereo: # If device doesn't have stereo camera pair
            if "mono" in self.save:
                self.save.remove("mono")
            if "depth" in self.save:
                self.save.remove("depth")

        pipeline, nodes = self.create_pipeline()

        streams = []
        if "color" in self.save: streams.append("color")
        if "depth" in self.save: streams.append("depth")
        if "mono" in self.save:
            streams.append("left")
            streams.append("right")

        self.frame_q = Queue(20)
        self.process = Process(target=store_frames, args=(self.path, self.frame_q, self.exit_stack))
        self.process.start()

        self.device.startPipeline(pipeline)

        self.queues = []
        for stream in streams:
            self.queues.append({
                'q': self.device.getOutputQueue(name=stream, maxSize=10, blocking=False),
                'msgs': [],
                'name': stream
            })


    def set_fps(self, fps: int):
        self.fps = fps

    def set_save_streams(self, save_streams):
        self.save = save_streams

    def create_folder(self, path: str, mxid: str) -> Path:
        i = 0
        while True:
            i += 1
            recordings_path = Path(path) / f"{i}-{mxid}"
            if not recordings_path.is_dir():
                recordings_path.mkdir(parents=True, exist_ok=False)
                return recordings_path

    def create_pipeline(self):
        pipeline = dai.Pipeline()
        nodes = types.SimpleNamespace()

        if "color" in self.save:
            nodes.color = pipeline.createColorCamera()
            nodes.color.setBoardSocket(dai.CameraBoardSocket.RGB)
            nodes.color.setResolution(dai.ColorCameraProperties.SensorResolution.THE_1080_P)
            nodes.color.setFps(self.fps)

            rgb_encoder = pipeline.createVideoEncoder()
            rgb_encoder.setDefaultProfilePreset(nodes.color.getVideoSize(), nodes.color.getFps(), dai.VideoEncoderProperties.Profile.MJPEG)
            # rgb_encoder.setLossless(True)
            nodes.color.video.link(rgb_encoder.input)

            # Create output for the rgb
            rgbOut = pipeline.createXLinkOut()
            rgbOut.setStreamName("color")
            rgb_encoder.bitstream.link(rgbOut.input)

        if "mono" or "depth" in self.save:
            # Create mono cameras
            nodes.left = pipeline.createMonoCamera()
            nodes.left.setResolution(dai.MonoCameraProperties.SensorResolution.THE_400_P)
            nodes.left.setBoardSocket(dai.CameraBoardSocket.LEFT)
            nodes.left.setFps(self.fps)

            nodes.right = pipeline.createMonoCamera()
            nodes.right.setResolution(dai.MonoCameraProperties.SensorResolution.THE_400_P)
            nodes.right.setBoardSocket(dai.CameraBoardSocket.RIGHT)
            nodes.right.setFps(self.fps)

            if "depth" in self.save:
                nodes.stereo = pipeline.createStereoDepth()
                nodes.stereo.initialConfig.setConfidenceThreshold(240)
                nodes.stereo.initialConfig.setMedianFilter(dai.StereoDepthProperties.MedianFilter.KERNEL_7x7)
                nodes.stereo.setLeftRightCheck(False)
                nodes.stereo.setExtendedDisparity(False)
                nodes.stereo.setSubpixel(False)

                nodes.left.out.link(nodes.stereo.left)
                nodes.right.out.link(nodes.stereo.right)

                depthOut = pipeline.createXLinkOut()
                depthOut.setStreamName("depth")
                nodes.stereo.depth.link(depthOut.input)


            # Create output
            if "mono" in self.save:
                left_encoder = pipeline.createVideoEncoder()
                left_encoder.setDefaultProfilePreset(nodes.left.getResolutionSize(), nodes.left.getFps(), dai.VideoEncoderProperties.Profile.MJPEG)
                # left_encoder.setLossless(True)
                nodes.left.out.link(left_encoder.input)
                # Create XLink output for left MJPEG stream
                leftOut = pipeline.createXLinkOut()
                leftOut.setStreamName("left")
                left_encoder.bitstream.link(leftOut.input)

                right_encoder = pipeline.createVideoEncoder()
                right_encoder.setDefaultProfilePreset(nodes.right.getResolutionSize(), nodes.right.getFps(), dai.VideoEncoderProperties.Profile.MJPEG)
                # right_encoder.setLossless(True)
                nodes.right.out.link(right_encoder.input)
                # Create XLink output for right MJPEG stream
                rightOut = pipeline.createXLinkOut()
                rightOut.setStreamName("right")
                right_encoder.bitstream.link(rightOut.input)

        self.nodes = nodes
        self.pipeline = pipeline
        return pipeline, nodes

