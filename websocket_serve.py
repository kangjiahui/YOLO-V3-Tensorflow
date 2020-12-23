# coding: utf-8

from __future__ import division, print_function

import base64

import tensorflow as tf
import numpy as np
import argparse
import cv2
import time
import json

from utils.misc_utils import parse_anchors, read_class_names
from utils.nms_utils import gpu_nms
from utils.plot_utils import get_color_table, plot_one_box
from utils.data_aug import letterbox_resize

from model import yolov3

import asyncio
import websockets
import cv2


parser = argparse.ArgumentParser(description="YOLO-V3 video test procedure.")
parser.add_argument("--input_video", type=str,default="D:/blibli/workspace_helmet01.mp4",
                    help="The path of the input video.")
parser.add_argument("--anchor_path", type=str, default="./data/yolo_anchors.txt",
                    help="The path of the anchor txt file.")
parser.add_argument("--new_size", nargs='*', type=int, default=[416, 416],
                    help="Resize the input image with `new_size`, size format: [width, height]")
parser.add_argument("--letterbox_resize", type=lambda x: (str(x).lower() == 'true'), default=True,
                    help="Whether to use the letterbox resize.")
parser.add_argument("--class_name_path", type=str, default="./data/coco.names",
                    help="The path of the class names.")
parser.add_argument("--restore_path", type=str, default="./checkpoint/best_model_Epoch_200_step_34370_mAP_0.8121_loss_9.4284_lr_1e-05",
                    help="The path of the weights to restore.")
parser.add_argument("--save_video", type=lambda x: (str(x).lower() == 'true'), default=False,
                    help="Whether to save the video detection results.")
args = parser.parse_args()

args.anchors = parse_anchors(args.anchor_path)
args.classes = read_class_names(args.class_name_path)
args.num_class = len(args.classes)

color_table = get_color_table(args.num_class)

sess = tf.Session()
input_data = tf.placeholder(tf.float32, [1, args.new_size[1], args.new_size[0], 3], name='input_data')
yolo_model = yolov3(args.num_class, args.anchors)
with tf.variable_scope('yolov3'):
    pred_feature_maps = yolo_model.forward(input_data, False)
pred_boxes, pred_confs, pred_probs = yolo_model.predict(pred_feature_maps)
pred_scores = pred_confs * pred_probs
boxes, scores, labels = gpu_nms(pred_boxes, pred_scores, args.num_class, max_boxes=200, score_thresh=0.3,
                                nms_thresh=0.45)
saver = tf.train.Saver()
saver.restore(sess, args.restore_path)


async def time_1(websocket, path):
    vid = cv2.VideoCapture(0)
    video_width = int(vid.get(3))
    video_height = int(vid.get(4))
    ROI = [int(video_width * 0.1), int(video_height * 0.1), int(video_width * 0.9),  int(video_height * 0.9)]  # The list represents minx,miny,maxx,maxy
    boxes_ori = []  # To convert result bboxes into origin image
    print("**************************start***************************")
    while vid.isOpened():
        ret, img_ori = vid.read()
        if not ret:
            break
        try:
            # Presenting region of interest by rectangle
            cv2.rectangle(img_ori, (ROI[0], ROI[1]), (ROI[2], ROI[3]), (255, 0, 0), 2)
            cv2.putText(img_ori, 'region of interest', (ROI[0], ROI[1]), 0, 5e-3 * 20, (0, 255, 0), 1)
            img_new = img_ori[int(ROI[1]): int(ROI[3]), int(ROI[0]): int(ROI[2])]
            if args.letterbox_resize:
                img, resize_ratio, dw, dh = letterbox_resize(img_new, args.new_size[0], args.new_size[1])
            else:
                height_ori, width_ori = img_ori.shape[:2]
                img = cv2.resize(img_ori, tuple(args.new_size))
            img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
            img = np.asarray(img, np.float32)
            img = img[np.newaxis, :] / 255.

            start_time = time.time()
            boxes_, scores_, labels_ = sess.run([boxes, scores, labels], feed_dict={input_data: img})
            print(boxes_)
            end_time = time.time()
            time_gap = (end_time - start_time)
            print("per frame needs time: {:.2f}".format(time_gap))

            # rescale the coordinates to the original image
            if args.letterbox_resize:
                boxes_[:, [0, 2]] = (boxes_[:, [0, 2]] - dw) / resize_ratio
                boxes_[:, [1, 3]] = (boxes_[:, [1, 3]] - dh) / resize_ratio
            else:
                boxes_[:, [0, 2]] *= (width_ori/float(args.new_size[0]))
                boxes_[:, [1, 3]] *= (height_ori/float(args.new_size[1]))

            box_list = []
            for i in range(len(boxes_)):
                x0, y0, x1, y1 = boxes_[i]
                x0 = x0 + ROI[0]
                y0 = y0 + ROI[1]
                x1 = x1 + ROI[0]
                y1 = y1 + ROI[1]
                boxes_ori.append([x0, y0, x1, y1])
                if scores_[i] > 0.7:
                    box_list.append({"box": [x0, y0, x1, y1], "label": args.classes[labels_[i]]})
                    plot_one_box(img_ori, [x0, y0, x1, y1], label=args.classes[labels_[i]] + ', {:.2f}%'.format(scores_[i] * 100), color=color_table[labels_[i]])
            # print(box_list)
            cv2.putText(img_ori, '{:.2f}ms'.format((end_time - start_time) * 1000), (40, 40), 0,
                        fontScale=1, color=(0, 255, 0), thickness=2)
            image = cv2.imencode('.jpg', img_ori)[1]
            image_code = str(base64.b64encode(image))[2:-1]
            flag = False
            for i in box_list:
                if i["label"] == "head":
                    flag = True
                    break
            result_json = json.dumps({"result": 0, "message": "SUCCESS", "image": image_code, "flag": flag})
        except Exception as e:
            print(e)
            msg = str(e)
            result_json = json.dumps({"result": -1, "message": msg})
        await websocket.send(result_json)
        if cv2.waitKey(1) & 0xFF == ord('q'):
            break
    vid.release()
    sess.close()
    # print("vid closed")


start_server = websockets.serve(time_1, "10.20.50.163", 5679)
print(' ========= websocket running =========')
asyncio.get_event_loop().run_until_complete(start_server)
asyncio.get_event_loop().run_forever()
