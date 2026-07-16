/*
 *   tjctl.c	A Linux userspace driver to control the TigerJet 560.
 *  
 *   version: 0.0.1
 *
 *   Copyright (C) 2004 Jonathan McDowell <noodles@earth.li>
 *
 *   Based on tjusb.c kernel driver by Ping Liu <pliu@tjnet.com>
 *   Portions copyright (C) 2003 TigerJet Network Inc.
 *
 *   This program is free software; you can redistribute it and/or modify
 *   it under the terms of the GNU General Public License as published by
 *   the Free Software Foundation; version 2 of the License.
 *
 *   This program is distributed in the hope that it will be useful,
 *   but WITHOUT ANY WARRANTY; without even the implied warranty of
 *   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 *   GNU General Public License for more details.
 *
 *   You should have received a copy of the GNU General Public License
 *   along with this program; if not, write to the Free Software
 *   Foundation, Inc., 675 Mass Ave, Cambridge, MA 02139, USA.
 */

#include <stdio.h>
#include <string.h>
#include <sys/types.h>
#include <sys/time.h>
#include <sys/unistd.h>
#include <stdbool.h>
#include <stdlib.h>
#include <signal.h>
#include <errno.h>
#include <sys/ioctl.h>
#include <linux/usbdevice_fs.h>
#include <usb.h>
#include "tjusb.h"
#include "proslic.h"

struct {
	unsigned int	vendor_id;
	unsigned int	product_id;
	char		*name;
} devlist[] = {
	{ 0x0000, 0x0000, "User-specified device" },
	{ 0x06e6, 0x831c, "TigerJet 560 FXS" },
};

int debug = 0;

static usb_dev_handle *bus_scan(unsigned int, unsigned int);

static int Tjusb_WriteTjRegs(usb_dev_handle *dev, unsigned char index,
		char *data, int len);
static int Tjusb_ReadTjRegs(usb_dev_handle *dev, unsigned char index,
		char *data, int len);
int readProSlicDirectReg(usb_dev_handle *dev, unsigned char address,
		char *dataRead);
int writeProSlicDirectReg(usb_dev_handle *dev, unsigned char address,
		unsigned char RegValue);

static alpha indirect_regs[] =
{
{0,"DTMF_ROW_0_PEAK",0x55C2},
{1,"DTMF_ROW_1_PEAK",0x51E6},
{2,"DTMF_ROW2_PEAK",0x4B85},
{3,"DTMF_ROW3_PEAK",0x4937},
{4,"DTMF_COL1_PEAK",0x3333},
{5,"DTMF_FWD_TWIST",0x0202},
{6,"DTMF_RVS_TWIST",0x0202},
{7,"DTMF_ROW_RATIO_TRES",0x0198},
{8,"DTMF_COL_RATIO_TRES",0x0198},
{9,"DTMF_ROW_2ND_ARM",0x0611},
{10,"DTMF_COL_2ND_ARM",0x0202},
{11,"DTMF_PWR_MIN_TRES",0x00E5},
{12,"DTMF_OT_LIM_TRES",0x0A1C},
{13,"OSC1_COEF",0x6D40},
{14,"OSC1X",0x0470},
{15,"OSC1Y",0x0000},
{16,"OSC2_COEF",0x4A80},
{17,"OSC2X",0x0830},
{18,"OSC2Y",0x0000},
{19,"RING_V_OFF",0x0000},
{20,"RING_OSC",0x7EF0},
{21,"RING_X",0x0160},
{22,"RING_Y",0x0000},
{23,"PULSE_ENVEL",0x2000},
{24,"PULSE_X",0x2000},
{25,"PULSE_Y",0x0000},
/*{26,"RECV_DIGITAL_GAIN",0x4000},	playback volume set lower */
{26,"RECV_DIGITAL_GAIN",0x2000},	/* playback volume set lower */
{27,"XMIT_DIGITAL_GAIN",0x4000},
{28,"LOOP_CLOSE_TRES",0x1000},
{29,"RING_TRIP_TRES",0x3600},
{30,"COMMON_MIN_TRES",0x1000},
{31,"COMMON_MAX_TRES",0x0200},
{32,"PWR_ALARM_Q1Q2",0x0550},
{33,"PWR_ALARM_Q3Q4",0x2600},
{34,"PWR_ALARM_Q5Q6",0x1B80},
{35,"LOOP_CLOSURE_FILTER",0x8000},
{36,"RING_TRIP_FILTER",0x0320},
{37,"TERM_LP_POLE_Q1Q2",0x0100},
{38,"TERM_LP_POLE_Q3Q4",0x0100},
{39,"TERM_LP_POLE_Q5Q6",0x0010},
{40,"CM_BIAS_RINGING",0x0C00},
{41,"DCDC_MIN_V",0x0C00},
{42,"DCDC_XTRA",0x1000},
};

static int Tjusb_WriteTjRegs(usb_dev_handle *dev, unsigned char index,
		char *data, int len)
{
	int requesttype;
	int res;

	requesttype = USB_TYPE_VENDOR | USB_RECIP_DEVICE;

	res = usb_control_msg(dev, requesttype, REQUEST_NORMAL, 0,
			index, data, len, 500);
	if (res == -ETIMEDOUT) {
		fprintf(stderr, "tjusb: timeout on vendor write\n");
		return -1;
	} else if (res < 0) {
		fprintf(stderr, "tjctl: Error executing control: status=%d\n",
				res);
		return -1;
	}

	return 0;
}

static int Tjusb_ReadTjRegs(usb_dev_handle *dev, unsigned char index,
		char *data, int len)
{
	int requesttype;
	int res;

	requesttype = USB_ENDPOINT_IN | USB_TYPE_VENDOR | USB_RECIP_DEVICE;

	res = usb_control_msg(dev, requesttype, REQUEST_NORMAL, 0,
			index, data, len, 500);

	if (res == -ETIMEDOUT) {
		fprintf(stderr, "tjusb: timeout on vendor read\n");
		return -1;
	} else if (res < 0) {
		fprintf(stderr, "tjusb: Error executing control: status=%d\n",
				res);
		return -1;
	}
	return 0;
}

/*
**	Write register to Tj560
*/
static int tjoutp(usb_dev_handle *dev, unsigned char address, char data)
{
	if (!Tjusb_WriteTjRegs(dev, address, &data, 1))
		return 0;

	return -1;
}

/*
**	read register from Tj560
*/
static int tjinp(usb_dev_handle *dev, unsigned char address, char *data)
{
	if (!Tjusb_ReadTjRegs(dev, address, data, 1))
		return 0;

	return -1;
}

/* Don't call from an interrupt context */
static int set_aux_ctrl(usb_dev_handle *dev, char uauxpins, int on)
{
	char udata12 = 0;
	char udata13 = 0;

	tjinp(dev, 0x12, &udata12);
	tjinp(dev, 0x13, &udata13);

	tjoutp(dev, 0x12, on ? (uauxpins | udata12) : (~uauxpins & udata12));
	tjoutp(dev, 0x13, uauxpins | udata13);

	return 0;
}

/*--------------------------------------------------
 *  ProSlic Functions
 *--------------------------------------------------
*/	
static int waitForProSlicIndirectRegAccess(usb_dev_handle *dev)
{
    char count, data;
    count = 0;
    while (count++ < 3)
	 {
		data = 0;
		readProSlicDirectReg(dev, I_STATUS, &data);

		if (!data)
			return 0;

	 }

    if(count > 2) fprintf(stderr, " ##### Loop error #####\n");

	return -1;
}

static int writeProSlicInDirectReg(usb_dev_handle *dev, unsigned char address,
		unsigned short data)
{
	
   if(!waitForProSlicIndirectRegAccess(dev))
	{
		if (!writeProSlicDirectReg(dev, IDA_LO,(unsigned char)(data & 0xFF)))
		{
			if(!writeProSlicDirectReg(dev, IDA_HI,(unsigned char)((data & 0xFF00)>>8)))
			{
				if(!writeProSlicDirectReg(dev, IAA,address))
					return 0;
			}
		}
	}

	return -1;
}

/*
**	Read register from ProSlic
*/
int readProSlicDirectReg(usb_dev_handle *dev, unsigned char address,
		char *dataRead)
{
	char data[4];

	data[0] = address | 0x80;
	data[1] = 0;
	data[2] = 0;
	data[3] = 0x67;

	/* write to TJ register 0x26 */
	Tjusb_WriteTjRegs(dev, TJ_SPORT0, data, 4);
	Tjusb_ReadTjRegs(dev, TJ_SPORT1, data, 1);
	*dataRead = data[0];

	return 0;
}

/*
**	Write register to ProSlic
*/
int writeProSlicDirectReg(usb_dev_handle *dev, unsigned char address,
		unsigned char RegValue)
{
	char data[4];

	data[0] = address & 0x7f;
	data[1] = RegValue;
	data[2] = 0;
	data[3] = 0x27;

	/* write to TJ register 0x26 */
	return Tjusb_WriteTjRegs(dev, TJ_SPORT0, data, 4);
}

static int readProSlicInDirectReg(usb_dev_handle *dev,
		unsigned char address, unsigned short *data)
{ 
    if (!waitForProSlicIndirectRegAccess(dev))
	 {
		if (!writeProSlicDirectReg(dev,IAA,address))
		{
			if(!waitForProSlicIndirectRegAccess(dev))
			{
				char data1, data2;

				if (!readProSlicDirectReg(dev,IDA_LO, &data1)
						&& !readProSlicDirectReg (dev,
							IDA_HI, &data2)) {
					*data = data1 | (data2 << 8);
					return 0;
				} else 
					fprintf(stderr,
						"Failed to read direct reg\n");
			} else
				fprintf(stderr, "Failed to wait inside\n");
		} else
			fprintf(stderr, "failed write direct IAA\n");
	 } else
	 	fprintf(stderr, "failed to wait\n");

    return -1;
}

static int initializeIndirectRegisters(usb_dev_handle *dev)
{
	unsigned char i;

	for (i=0; i<43; i++)
	{
		if(writeProSlicInDirectReg(dev, i,indirect_regs[i].initial))
			return -1;
	}

	return 0;
}

static int verifyIndirectRegisters(usb_dev_handle *dev)
{ 
	int passed = 1;
	unsigned short i,j, initial;

	for (i=0; i<43; i++) 
	{
		if(readProSlicInDirectReg(dev, (unsigned char) i, &j)) {
			fprintf(stderr, "Failed to read indirect register %d\n", i);
			return -1;
		}
		initial= indirect_regs[i].initial;

		if ( j != initial )
		{
			 fprintf(stderr, "!!!!!!! %s  iREG %X = %X  should be %X\n",
				indirect_regs[i].name,i,j,initial );
			 passed = 0;
		}	
	}

    if (passed) {
		if (debug)
			fprintf(stderr, "Init Indirect Registers completed successfully.\n");
    } else {
		fprintf(stderr, " !!!!! Init Indirect Registers UNSUCCESSFULLY.\n");
    }

	return 0;
}

static int calibrateAndActivateProSlic(usb_dev_handle *dev)
{ 
	char x;

	if(writeProSlicDirectReg(dev, 92, 0xc8))
		 return -1;

	if(writeProSlicDirectReg(dev, 97, 0))
		 return -1;

	if(writeProSlicDirectReg(dev, 93, 0x19))
		 return -1;

	if(writeProSlicDirectReg(dev, 14, 0))
		 return -1;

	if(writeProSlicDirectReg(dev, 93, 0x99))
		 return -1;

	if(!readProSlicDirectReg (dev, 93, &x))
	{
		if (debug)
			fprintf(stderr, "DC Cal x=%x\n",x);

		if (!writeProSlicDirectReg(dev, 97, 0))
		{
		   if(!writeProSlicDirectReg(dev, CALIBR1, CALIBRATE_LINE))
			{
				char data;

				if(!readProSlicDirectReg(dev, CALIBR1, &data))
					 return writeProSlicDirectReg(dev,
							 LINE_STATE,
							 ACTIVATE_LINE);
			}
		}
	}

	return -1;
}

static int InitProSlic(usb_dev_handle *dev)
{
	if (writeProSlicDirectReg(dev, 67, 0x0e))  {
		/* Disable Auto Power Alarm Detect and other "features" */
		return -1;
	}
	if (initializeIndirectRegisters(dev)) {
		fprintf(stderr, "Indirect Registers failed to initialize.\n");
		return -1;
	}
	if (verifyIndirectRegisters(dev)) {
		fprintf(stderr, "Indirect Registers failed verification.\n");
		return -1;
	}
	if (calibrateAndActivateProSlic(dev)) {
		fprintf(stderr, "ProSlic Died on Activation.\n");
		 return -1;
	}
	if (writeProSlicInDirectReg(dev, 97, 0x0)) {
		/* Stanley: for the bad recording fix */
		fprintf(stderr, "ProSlic IndirectReg Died.\n");
		return -1;
	}
	if (writeProSlicDirectReg(dev, 1, 0x2a)) {
		/* U-Law GCI 8-bit interface */
		fprintf(stderr, "ProSlic DirectReg Died.\n");
		return -1;
	}
	/* Tx Start count low byte 0 */
	if (writeProSlicDirectReg(dev, 2, 0))
		return -1;
	/* Tx Start count high byte 0 */
	if (writeProSlicDirectReg(dev, 3, 0))
		return -1;
	/* Rx Start count low byte 0 */
	if (writeProSlicDirectReg(dev, 4, 0))
		return -1;
	/* Rx Start count high byte 0 */
	if (writeProSlicDirectReg(dev, 5, 0))
		return -1;
	/* disable loopback */
	if (writeProSlicDirectReg(dev, 8, 0x0))
		return -1;
	/* clear all interrupts */
	if (writeProSlicDirectReg(dev, 18, 0xff))
		return -1;
	if (writeProSlicDirectReg(dev, 19, 0xff)) 
		return -1;
	if (writeProSlicDirectReg(dev, 20, 0xff)) 
		return -1;
	/* enable interrupt */
	if (writeProSlicDirectReg(dev, 21, 0x00))
		return -1;
 	/* Loop detection interrupt */
	if (writeProSlicDirectReg(dev, 22, 0x02))
		return -1;
 	/* DTMF detection interrupt */
	if (writeProSlicDirectReg(dev, 23, 0x01))
		return -1;
	return 0;
}

static int init_hardware(usb_dev_handle *dev)
{
	if (tjoutp(dev, 0x12, 0x00))	/* AUX6 as output, set to low */
		return -1;
    	if (tjoutp(dev, 0x13, 0x40))	/* AUX6 is output */
      		return -1;
    	if (tjoutp(dev, 0, 0x50))	/* extrst, AUX2 is suspend */
      		return -1;
    	if (tjoutp(dev, 0x29, 0x20))	/* enable SerialUP AUX pin definition */
      		return -1;
    	if (tjoutp(dev, 0, 0x51))	/* no extrst, AUX2 is suspend */
      		return -1;
	/* Make sure there is no gain */
	if (tjoutp(dev, 0x22, 0x00))
		return -1;
    	if (tjoutp(dev, 0x23, 0xf2))
       		return -1;
    	if (tjoutp(dev, 0x24, 0x00))
		return -1;
    	if (tjoutp(dev, 0x25, 0xc9))
		return -1;
	if (InitProSlic(dev)) {
		fprintf(stderr, "tjusb: Failed to initialize proslic\n");
		return -1;
	}
	return 0; 
}

void usage()
{
	printf("tjctl 0.0.1 by Jonathan McDowell <noodles@earth.li>\n"
			"Usage: tjctl <command> [options]\n"
			"\tValid options are:\n"
			"\tinit    -- initialize the tj560\n"
			"\tring    -- make the phone ring\n"
			"\tnoring  -- stop the phone ringing\n"
			"\tgethook -- get the on/off hook status\n"
	      );
}

int main(int argc, char **argv)
{
	int c;
	usb_dev_handle *tjdev;			/* libusb handle */

	if (argc < 2) {
		usage();
		exit(EXIT_FAILURE);
	}

	usb_set_debug(debug);
	usb_init();
	usb_find_busses();
	usb_find_devices();

	for (c = 0; c < sizeof(devlist) / sizeof(devlist[0]); c++) {
		if (devlist[c].product_id == 0 && devlist[c].vendor_id == 0) {
			continue;
		}
		tjdev = bus_scan(devlist[c].vendor_id, devlist[c].product_id);
		if (tjdev) {
			break;
		}
	}

	if (tjdev == NULL) {
		fprintf(stderr, "Error: Couldn't locate device\n");
		exit(EXIT_FAILURE);
	}

	if (debug) {
		fprintf(stderr, "Found %s\n", devlist[c].name);
	}

	usb_claim_interface(tjdev, 0);

	if (!strcmp("init", argv[1])) {
		init_hardware(tjdev);
	} else if (!strcmp("ring", argv[1])) {
		writeProSlicDirectReg(tjdev, 64, 0x44);
	} else if (!strcmp("noring", argv[1])) {
		writeProSlicDirectReg(tjdev, 64, 0x11);
	} else if (!strcmp("gethook", argv[1])) {
		char x;
		readProSlicDirectReg(tjdev, 68, &x);
		printf("%shook\n", (x == 1) ? "off" : "on");
	} else {
		usage();
	}

	usb_release_interface(tjdev, 0);
	usb_close(tjdev);

	return EXIT_SUCCESS;
}

static usb_dev_handle * bus_scan(unsigned int vendor, unsigned int product)
{
	struct usb_bus		*bus;
	struct usb_device	*device;
	usb_dev_handle		*result;

	for (bus = usb_busses; bus; bus = bus->next) {
		for (device = bus->devices; device; device = device->next) {
			if (device->descriptor.idVendor == vendor &&
			    device->descriptor.idProduct == product) {
				result = usb_open(device);
				return result;
			}
		}
	}
	return NULL;
}
